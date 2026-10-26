// Package gateway 是浏览器到控制面的 HTTP 入口（架构 §2 Gateway 的 MVP 形态）。
//
// 回复不直连模型，走 session → dispatcher → worker 的 NATS 链路。
//
// 单写者约束：session actor 全进程唯一，gateway 必须与 dispatcher 同进程、
// 共享同一个 session.Manager（cmd/runtime 装配）。拆成独立进程的前提是
// SessionService（Slice D）提供 control RPC。
package gateway

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/live"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/Darth-S1d1ous/Termitaria/internal/swarm"
)

const (
	savedChatID = "saved"
	userID      = "user"
)

var (
	errChatNotFound  = errors.New("not found")
	errSavedDisabled = errors.New("saved messages are not available")
)

type agentJSON struct {
	ID          string   `json:"id"`
	DisplayName string   `json:"display_name"`
	Kind        string   `json:"kind"`
	RolePrompt  string   `json:"role_prompt,omitempty"`
	Tools       []string `json:"tools,omitempty"`
	Skills      []string `json:"skills,omitempty"`
}

type seedJSON struct {
	ID    string `json:"id"`
	Kind  string `json:"kind"`
	Title string `json:"title"`
	Body  string `json:"body"`
}

type artifactJSON struct {
	Name  string `json:"name"`
	Kind  string `json:"kind"`
	Bytes int    `json:"bytes"`
}

type projectJSON struct {
	ID          string             `json:"id"`
	Name        string             `json:"name"`
	Goal        string             `json:"goal"`
	Deliverable *swarm.Deliverable `json:"deliverable,omitempty"`
	Agents      []agentJSON        `json:"agents"`
	Steps       []stepJSON         `json:"steps,omitempty"`
	Seeds       []seedJSON         `json:"seeds,omitempty"`
	Artifacts   []artifactJSON     `json:"artifacts,omitempty"`
}

type stateFile struct {
	Projects []*projectJSON    `json:"projects"`
	Sessions map[string]string `json:"sessions"` // "<project>/<chat>" → session_id
}

// AgentStarter 把 SwarmSpec 里的 agent 登记进控制面并标为在线。
// 空 rolePrompt / maxTokens 表示保留已有配置，只确认这个 agent 已被拉起。
type AgentStarter interface {
	Start(id, rolePrompt string, maxTokens uint32)
}

type specJSON struct {
	Name string `json:"name"`
	Goal string `json:"goal"`
	YAML string `json:"yaml"`
}

type Server struct {
	manager      *session.Manager
	template     *projectJSON // 未带 SwarmSpec 时，新建项目克隆的名册
	specs        []specJSON
	starter      AgentStarter
	replyTimeout time.Duration
	hub          *live.Hub
	fanout       interface{ Stop() }
	bus          *bus.Bus

	mu       sync.Mutex
	path     string
	swarmDir string
	projects []*projectJSON
	sessions map[string]string
	inflight map[string]struct{} // project/agent：这个同事已经有一单在做
}

// New 装载状态文件；首次启动为每个 spec 建一个默认项目（id = spec 名）。
func New(manager *session.Manager, specs []*swarm.Spec, statePath string, replyTimeout time.Duration, hub *live.Hub, starter AgentStarter, swarmDir string) (*Server, error) {
	if hub == nil {
		hub = live.New()
	}
	s := &Server{
		manager:      manager,
		starter:      starter,
		replyTimeout: replyTimeout,
		hub:          hub,
		path:         statePath,
		swarmDir:     swarmDir,
		sessions:     map[string]string{},
		inflight:     map[string]struct{}{},
	}
	for _, spec := range specs {
		s.specs = append(s.specs, specJSON{Name: spec.Name, Goal: spec.Goal, YAML: spec.Raw})
		s.startSpec(spec)
		if spec.Name == "autoresearch" {
			s.template = projectFromSpec(spec.Name, spec)
		}
	}
	if s.template == nil {
		s.template = projectFromSpec(specs[0].Name, specs[0])
	}
	if raw, err := os.ReadFile(statePath); err == nil {
		var f stateFile
		if err := json.Unmarshal(raw, &f); err != nil {
			return nil, err
		}
		s.projects, s.sessions = f.Projects, f.Sessions
		if s.sessions == nil {
			s.sessions = map[string]string{}
		}
		for _, p := range s.projects {
			if p.Deliverable == nil {
				p.Deliverable = swarm.CompileDeliverable(swarm.Deliverable{}, p.Goal)
			}
			s.startProject(p)
		}
	} else if os.IsNotExist(err) {
		for _, spec := range specs {
			s.projects = append(s.projects, projectFromSpec(spec.Name, spec))
		}
		if err := s.save(); err != nil {
			return nil, err
		}
	} else {
		return nil, err
	}
	return s, nil
}

func projectFromSpec(id string, spec *swarm.Spec) *projectJSON {
	p := &projectJSON{
		ID:          id,
		Name:        titleName(spec.Name),
		Goal:        spec.Goal,
		Deliverable: swarm.CompileDeliverable(spec.Deliverable, spec.Goal),
	}
	for _, a := range spec.Agents {
		p.Agents = append(p.Agents, agentJSON{
			ID: a.ID, DisplayName: a.DisplayName, Kind: a.Kind,
			RolePrompt: a.RolePrompt,
			Tools:      append([]string(nil), a.Tools...),
			Skills:     append([]string(nil), a.Skills...),
		})
	}
	return p
}

// startSpec 按声明拉起 spec 里的每个 agent。
func (s *Server) startSpec(spec *swarm.Spec) {
	if s.starter == nil || spec == nil {
		return
	}
	for _, agent := range spec.Agents {
		s.starter.Start(agent.ID, agent.RolePrompt, agent.Budget.MaxTokens)
	}
}

// startProject 重启后把已有项目的 agent 再拉起一次。没有 prompt 时保留登记表里的角色。
func (s *Server) startProject(p *projectJSON) {
	if s.starter == nil || p == nil {
		return
	}
	for _, agent := range p.Agents {
		s.starter.Start(agent.ID, "", 0)
	}
}

func (s *Server) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /api/swarmspecs", s.listSpecs)
	mux.HandleFunc("GET /api/models", s.listModels)
	mux.HandleFunc("GET /api/projects", s.listProjects)
	mux.HandleFunc("POST /api/projects", s.createProject)
	mux.HandleFunc("DELETE /api/projects/{id}", s.deleteProject)
	mux.HandleFunc("GET /api/projects/{id}/chats", s.listChats)
	mux.HandleFunc("GET /api/projects/{id}/chats/{chat}/messages", s.listMessages)
	mux.HandleFunc("POST /api/projects/{id}/chats/{chat}/messages", s.postMessage)
	mux.HandleFunc("GET /api/projects/{id}/topology", s.topology)
	mux.HandleFunc("POST /api/projects/{id}/delegate", s.delegate)
	mux.HandleFunc("POST /api/projects/{id}/steps", s.updateStep)
	mux.HandleFunc("POST /api/projects/{id}/artifacts", s.createArtifact)
	mux.HandleFunc("GET /api/projects/{id}/artifacts/{name}", s.getArtifact)
	mux.HandleFunc("GET /api/projects/{id}/knowledge", s.projectGraph)
	mux.HandleFunc("POST /api/projects/{id}/knowledge", s.uploadKnowledge)
	mux.HandleFunc("GET /api/memory-graph", s.memoryGraph)
	mux.HandleFunc("GET /api/ws", s.serveWS)
	return cors(mux)
}

func (s *Server) ListenAndServe(addr string) error {
	log.Printf("gateway http://%s", addr)
	return http.ListenAndServe(addr, s.Handler())
}

func (s *Server) memoryGraph(w http.ResponseWriter, r *http.Request) {
	agent := strings.TrimSpace(r.URL.Query().Get("agent"))
	if agent == "" {
		writeError(w, 400, "agent is required")
		return
	}
	addr := os.Getenv("MEMORY_HTTP_ADDR")
	if addr == "" {
		addr = "127.0.0.1:8791"
	}
	endpoint := "http://" + addr + "/graph?agent=" + url.QueryEscape(agent)
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	res, err := http.DefaultClient.Do(req)
	if err != nil {
		writeJSON(w, 200, map[string]any{
			"backend":  "",
			"agent_id": agent,
			"nodes":    []any{},
			"edges":    []any{},
		})
		return
	}
	defer res.Body.Close()
	body, err := io.ReadAll(io.LimitReader(res.Body, 1<<20))
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(res.StatusCode)
	_, _ = w.Write(body)
}

func cors(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		if r.Method == http.MethodOptions {
			w.WriteHeader(204)
			return
		}
		next.ServeHTTP(w, r)
	})
}

// ----- projects -----

func (s *Server) listProjects(w http.ResponseWriter, r *http.Request) {
	s.mu.Lock()
	defer s.mu.Unlock()
	writeJSON(w, 200, map[string]any{"projects": s.projects})
}

func (s *Server) listSpecs(w http.ResponseWriter, r *http.Request) {
	// 目录在进程启动后还会增删。每次打开向导都重读，新 SwarmSpec 不用重启就能出现在模板里。
	if s.swarmDir != "" {
		if loaded, err := swarm.LoadDir(s.swarmDir); err != nil {
			log.Printf("reload swarmdir %s: %v", s.swarmDir, err)
		} else {
			specs := make([]specJSON, 0, len(loaded))
			for _, spec := range loaded {
				s.startSpec(spec)
				specs = append(specs, specJSON{Name: spec.Name, Goal: spec.Goal, YAML: spec.Raw})
			}
			s.mu.Lock()
			s.specs = specs
			s.mu.Unlock()
		}
	}
	s.mu.Lock()
	specs := append([]specJSON(nil), s.specs...)
	s.mu.Unlock()
	writeJSON(w, 200, map[string]any{"specs": specs})
}

func (s *Server) createProject(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name  string     `json:"name"`
		Goal  string     `json:"goal"`
		Spec  string     `json:"spec"`
		Seeds []seedJSON `json:"seeds"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	name := strings.TrimSpace(body.Name)
	var compiled *swarm.Spec
	if strings.TrimSpace(body.Spec) != "" {
		spec, err := swarm.Parse([]byte(body.Spec))
		if err != nil {
			writeError(w, 400, err.Error())
			return
		}
		if err := checkSkills(spec); err != nil {
			writeError(w, 400, err.Error())
			return
		}
		compiled = spec
	}
	seeds, err := normalizeSeeds(body.Seeds)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	if name == "" && compiled == nil {
		writeError(w, 400, "name is required")
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	var p *projectJSON
	if compiled != nil {
		p = projectFromSpec("p_"+randHex(6), compiled)
		if name != "" {
			p.Name = name
		}
		s.startSpec(compiled)
	} else {
		p = &projectJSON{
			ID:     "p_" + randHex(6),
			Name:   name,
			Goal:   strings.TrimSpace(body.Goal),
			Agents: append([]agentJSON(nil), s.template.Agents...),
		}
		if p.Goal == "" {
			p.Goal = s.template.Goal
		}
		s.startProject(p)
	}
	p.Seeds = seeds
	for _, seed := range p.Seeds {
		mime := "text/plain"
		if seed.Kind == "link" {
			mime = "application/link"
		}
		s.publishDocument(r.Context(), p.ID, seed.ID, seed.Title, seed.Body, mime)
	}
	p.Steps = newPlan(p.Goal)
	if err := s.announcePlan(r.Context(), p); err != nil {
		writeError(w, 502, err.Error())
		return
	}
	s.projects = append(s.projects, p)
	if err := s.save(); err != nil {
		writeError(w, 502, err.Error())
		return
	}
	writeJSON(w, 201, p)
}

func (s *Server) deleteProject(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	s.mu.Lock()
	defer s.mu.Unlock()
	for i, p := range s.projects {
		if p.ID != id {
			continue
		}
		if err := s.manager.ForgetSwarm(r.Context(), id); err != nil {
			writeError(w, 502, err.Error())
			return
		}
		s.projects = append(s.projects[:i], s.projects[i+1:]...)
		for key := range s.sessions {
			if strings.HasPrefix(key, id+"/") {
				delete(s.sessions, key)
			}
		}
		if err := s.save(); err != nil {
			writeError(w, 502, err.Error())
			return
		}
		w.WriteHeader(204)
		return
	}
	writeError(w, 404, "not found")
}

// ----- chats -----

type chatJSON struct {
	ID              string `json:"id"`
	Kind            string `json:"kind"`
	Title           string `json:"title"`
	AgentKind       string `json:"agent_kind"`
	Pinned          bool   `json:"pinned"`
	Enabled         bool   `json:"enabled"`
	LastMessage     string `json:"last_message"`
	LastRole        string `json:"last_role"`
	LastMessageAtMs int64  `json:"last_message_at_ms"`
}

func (s *Server) listChats(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	p := s.project(projectID)
	if p == nil {
		writeError(w, 404, "not found")
		return
	}
	rows := orderChats(p)
	for i := range rows {
		msgs, err := s.messagesFor(r.Context(), projectID, rows[i].ID)
		if err != nil || len(msgs) == 0 {
			continue
		}
		last := msgs[len(msgs)-1]
		rows[i].LastMessage = last.Text
		rows[i].LastRole = last.Role
		rows[i].LastMessageAtMs = last.CreatedAtMs
	}
	writeJSON(w, 200, map[string]any{"chats": rows})
}

// orderChats：orchestrator 置顶，Saved Messages 第二，其余按显示名排序（沿用 clientapi 规则）。
func orderChats(p *projectJSON) []chatJSON {
	key := func(a agentJSON) string { return strings.ToLower(a.DisplayName) + "\x00" + a.ID }
	var orchestrators, workers []agentJSON
	for _, a := range p.Agents {
		if a.Kind == "ORCHESTRATOR" {
			orchestrators = append(orchestrators, a)
		} else {
			workers = append(workers, a)
		}
	}
	sort.Slice(orchestrators, func(i, j int) bool { return key(orchestrators[i]) < key(orchestrators[j]) })
	sort.Slice(workers, func(i, j int) bool { return key(workers[i]) < key(workers[j]) })
	rows := []chatJSON{{
		ID: channelChatID, Kind: "channel", Title: "General", Pinned: true, Enabled: true,
	}}
	rows = append(rows, chatJSON{ID: savedChatID, Kind: "saved", Title: "Saved Messages", Enabled: false})
	for _, a := range orchestrators {
		rows = append(rows, chatJSON{ID: a.ID, Kind: "agent", Title: a.DisplayName, AgentKind: a.Kind, Pinned: true, Enabled: true})
	}
	for _, a := range workers {
		rows = append(rows, chatJSON{ID: a.ID, Kind: "agent", Title: a.DisplayName, AgentKind: a.Kind, Enabled: true})
	}
	return rows
}

// ----- messages -----

type messageJSON struct {
	ID          string `json:"id"`
	Role        string `json:"role"`
	Text        string `json:"text"`
	CreatedAtMs int64  `json:"created_at_ms"`
	AgentID     string `json:"agent_id,omitempty"`
	Sender      string `json:"sender,omitempty"`
	SessionID   string `json:"session_id,omitempty"`
}

func (s *Server) listMessages(w http.ResponseWriter, r *http.Request) {
	projectID, chatID := r.PathValue("id"), r.PathValue("chat")
	messages, err := s.messagesFor(r.Context(), projectID, chatID)
	if err != nil {
		writeError(w, 404, "not found")
		return
	}
	writeJSON(w, 200, map[string]any{"messages": messages})
}

func (s *Server) postMessage(w http.ResponseWriter, r *http.Request) {
	projectID, chatID := r.PathValue("id"), r.PathValue("chat")
	var body struct {
		Text           string `json:"text"`
		MentionAgentID string `json:"mention_agent_id"`
		ModelID        string `json:"model_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	text := strings.TrimSpace(body.Text)
	if text == "" {
		writeError(w, 400, "text is required")
		return
	}
	if chatID == savedChatID {
		writeError(w, 409, "saved messages are not available")
		return
	}
	agentID, key, err := s.sendTarget(projectID, chatID, body.MentionAgentID)
	if err != nil {
		writeSendError(w, err)
		return
	}
	model, err := resolveModel(body.ModelID)
	if err != nil {
		writeError(w, 400, err.Error())
		return
	}
	sessionID, err := s.ensureSession(r.Context(), projectID, key, agentID)
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	userMsg, err := s.manager.AppendMessageWithModel(r.Context(), sessionID,
		&sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: userID}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: text}}},
		"", ulid.Make().String(), "", model)
	if err != nil {
		status := 502
		if errors.Is(err, session.ErrNotYourTurn) {
			status = 409 // agent 还在想：前端禁用输入时不会发生，直发 API 时提示稍候
		}
		writeError(w, status, err.Error())
		return
	}
	// 这是这次 HTTP 请求的等待上限。任务还在跑，回复稍后仍会落进会话。
	// 步骤失败只看任务结果：取消，或失败且错误码含 BUDGET / TIMEOUT。
	agentMsg, err := s.waitReply(r.Context(), sessionID, userMsg.GetMessageId())
	if err != nil {
		writeError(w, 504, "The agent reply timed out. The worker is running, but the model is slow. Refresh later to see the result.")
		return
	}
	writeJSON(w, 200, map[string]any{
		"user":  s.messageJSON(projectID, userMsg),
		"agent": s.messageJSON(projectID, agentMsg),
	})
}

func (s *Server) sendTarget(projectID, chatID, mention string) (agentID, key string, err error) {
	if s.project(projectID) == nil {
		return "", "", errChatNotFound
	}
	if chatID == savedChatID {
		return "", "", errSavedDisabled
	}
	if chatID == channelChatID {
		agentID, err = ChannelAgent(s.project(projectID).Agents, mention)
		if err != nil {
			return "", "", err
		}
		return agentID, sessionKey(projectID, chatID, agentID), nil
	}
	if s.agent(projectID, chatID) == nil {
		return "", "", errChatNotFound
	}
	return chatID, sessionKey(projectID, chatID, chatID), nil
}

func writeSendError(w http.ResponseWriter, err error) {
	switch {
	case errors.Is(err, errSavedDisabled):
		writeError(w, 409, err.Error())
	case errors.Is(err, errUnknownAgent), errors.Is(err, errNoOrchestrator):
		writeError(w, 400, err.Error())
	default:
		writeError(w, 404, "not found")
	}
}

// ensureSession 找或开一条 user↔agent 的 1:1 session。
// 用户聊天不限轮次、不自动关闭（idle/max_turns 全 0），否则聊几天就 ErrSessionClosed。
// key 把频道和私聊分开，agentID 是会话的另一端。
func (s *Server) ensureSession(ctx context.Context, projectID, key, agentID string) (string, error) {
	s.mu.Lock()
	sessionID, ok := s.sessions[key]
	s.mu.Unlock()
	if ok {
		return sessionID, nil
	}
	sess, err := s.manager.OpenSession(ctx, projectID,
		[]*sessionv1.Participant{
			{Kind: &sessionv1.Participant_UserId{UserId: userID}},
			{Kind: &sessionv1.Participant_AgentId{AgentId: agentID}},
		},
		&sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT},
		"")
	if err != nil {
		return "", err
	}
	s.mu.Lock()
	s.sessions[key] = sess.GetSessionId()
	err = s.save()
	s.mu.Unlock()
	return sess.GetSessionId(), err
}

// waitReply 轮询窗口，等 worker 结果经 dispatcher 落流（reply_to = 触发消息）。
// worker FAILED 不落消息，只能等超时——orchestrator 介入是 Slice C 的事。
func (s *Server) waitReply(ctx context.Context, sessionID, userMsgID string) (*sessionv1.Message, error) {
	return s.waitReplyFor(ctx, sessionID, userMsgID, s.replyTimeout)
}

func (s *Server) waitReplyFor(ctx context.Context, sessionID, userMsgID string, timeout time.Duration) (*sessionv1.Message, error) {
	deadline := time.Now().Add(timeout)
	for {
		snap, err := s.manager.Snapshot(ctx, sessionID, 0)
		if err != nil {
			return nil, err
		}
		for _, m := range snap.Window {
			if m.GetReplyToMessageId() == userMsgID && m.GetFrom().GetAgentId() != "" {
				return m, nil
			}
		}
		if time.Now().After(deadline) {
			return nil, errors.New("reply timeout")
		}
		select {
		case <-ctx.Done():
			return nil, ctx.Err()
		case <-time.After(500 * time.Millisecond):
		}
	}
}

// messagesFor 返回某个聊天行的消息。空列表是非 nil 切片，避免 JSON null。
func (s *Server) messagesFor(ctx context.Context, projectID, chatID string) ([]messageJSON, error) {
	if s.project(projectID) == nil {
		return nil, errChatNotFound
	}
	if chatID == savedChatID {
		return []messageJSON{}, nil
	}
	if chatID == channelChatID {
		return s.channelMessages(ctx, projectID), nil
	}
	if s.agent(projectID, chatID) == nil {
		return nil, errChatNotFound
	}
	return s.windowJSON(projectID, s.snapshotKey(ctx, sessionKey(projectID, chatID, chatID))), nil
}

// channelMessages 合并频道 session，以及 agent 之间的 mesh session。私聊不进来。
func (s *Server) channelMessages(ctx context.Context, projectID string) []messageJSON {
	s.mu.Lock()
	var channelIDs []string
	dm := map[string]bool{}
	for key, id := range s.sessions {
		switch {
		case isChannelKey(projectID, key):
			channelIDs = append(channelIDs, id)
		case isDMKey(projectID, key):
			dm[id] = true
		}
	}
	s.mu.Unlock()

	var out []messageJSON
	seen := map[string]bool{}
	for _, id := range channelIDs {
		seen[id] = true
		out = append(out, s.windowJSON(projectID, s.snapshotSession(ctx, id))...)
	}
	list, err := s.manager.ListSessions(ctx, projectID, sessionv1.SessionState_SESSION_STATE_UNSPECIFIED)
	if err == nil {
		for _, sess := range list {
			if dm[sess.GetSessionId()] || seen[sess.GetSessionId()] || !bothAgents(sess) {
				continue
			}
			out = append(out, s.windowJSON(projectID, s.snapshotSession(ctx, sess.GetSessionId()))...)
		}
	}
	sort.Slice(out, func(i, j int) bool {
		if out[i].CreatedAtMs == out[j].CreatedAtMs {
			return out[i].ID < out[j].ID
		}
		return out[i].CreatedAtMs < out[j].CreatedAtMs
	})
	if out == nil {
		out = []messageJSON{}
	}
	return out
}

func (s *Server) snapshotKey(ctx context.Context, key string) *session.Snapshot {
	s.mu.Lock()
	sessionID, ok := s.sessions[key]
	s.mu.Unlock()
	if !ok {
		return nil
	}
	return s.snapshotSession(ctx, sessionID)
}

func (s *Server) snapshotSession(ctx context.Context, sessionID string) *session.Snapshot {
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	snap, err := s.manager.Snapshot(ctx, sessionID, 0)
	if err != nil {
		log.Printf("snapshot %s: %v", sessionID, err)
		return nil
	}
	return snap
}

func (s *Server) windowJSON(projectID string, snap *session.Snapshot) []messageJSON {
	if snap == nil {
		return []messageJSON{}
	}
	out := make([]messageJSON, 0, len(snap.Window))
	for _, message := range snap.Window {
		out = append(out, s.messageJSON(projectID, message))
	}
	return out
}

// ----- helpers -----

func (s *Server) project(id string) *projectJSON {
	s.mu.Lock()
	defer s.mu.Unlock()
	for _, p := range s.projects {
		if p.ID == id {
			return p
		}
	}
	return nil
}

func (s *Server) agent(projectID, agentID string) *agentJSON {
	p := s.project(projectID)
	if p == nil {
		return nil
	}
	for _, a := range p.Agents {
		if a.ID == agentID {
			return &a
		}
	}
	return nil
}

func (s *Server) save() error {
	raw, err := json.MarshalIndent(stateFile{Projects: s.projects, Sessions: s.sessions}, "", "  ")
	if err != nil {
		return err
	}
	if err := os.MkdirAll(filepath.Dir(s.path), 0o755); err != nil {
		return err
	}
	tmp := s.path + ".tmp"
	if err := os.WriteFile(tmp, raw, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, s.path)
}

func roleOf(m *sessionv1.Message) string {
	if m.GetFrom().GetUserId() != "" {
		return "user"
	}
	return "agent"
}

func messageText(m *sessionv1.Message) string {
	var parts []string
	for _, b := range m.GetContent() {
		if t := b.GetText(); t != "" {
			parts = append(parts, t)
		}
	}
	return strings.Join(parts, "\n")
}

func (s *Server) messageJSON(projectID string, m *sessionv1.Message) messageJSON {
	item := messageJSON{
		ID:          m.GetMessageId(),
		Role:        roleOf(m),
		Text:        messageText(m),
		CreatedAtMs: m.GetCreatedAtMs(),
		SessionID:   m.GetSessionId(),
	}
	if id := m.GetFrom().GetAgentId(); id != "" {
		item.AgentID = id
		item.Sender = id
		if agent := s.agent(projectID, id); agent != nil {
			item.Sender = agent.DisplayName
		}
	}
	return item
}

// titleName："autoresearch-lab" → "Autoresearch Lab"（默认项目的显示名）。
func titleName(name string) string {
	words := strings.Fields(strings.ReplaceAll(name, "-", " "))
	for i, w := range words {
		words[i] = strings.ToUpper(w[:1]) + w[1:]
	}
	return strings.Join(words, " ")
}

func randHex(n int) string {
	buf := make([]byte, n)
	_, _ = rand.Read(buf)
	return hex.EncodeToString(buf)
}

func writeJSON(w http.ResponseWriter, status int, payload any) {
	raw, err := json.Marshal(payload)
	if err != nil {
		status = 502
		raw, _ = json.Marshal(map[string]string{"error": err.Error()})
	}
	w.Header().Set("Content-Type", "application/json; charset=utf-8")
	w.WriteHeader(status)
	_, _ = w.Write(raw)
}

func writeError(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]string{"error": msg})
}
