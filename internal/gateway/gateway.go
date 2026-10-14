// Package gateway 是浏览器到控制面的 HTTP 入口（架构 §2 Gateway 的 MVP 形态）。
//
// 契约与已删除的 services/clientapi 完全一致，前端零改动；
// 区别在于回复不再直连模型，而是走 session → dispatcher → worker 的 NATS 链路。
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
	"log"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/oklog/ulid/v2"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
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
	ID          string `json:"id"`
	DisplayName string `json:"display_name"`
	Kind        string `json:"kind"`
}

type projectJSON struct {
	ID     string      `json:"id"`
	Name   string      `json:"name"`
	Goal   string      `json:"goal"`
	Agents []agentJSON `json:"agents"`
}

type stateFile struct {
	Projects []*projectJSON    `json:"projects"`
	Sessions map[string]string `json:"sessions"` // "<project>/<chat>" → session_id
}

type Server struct {
	manager      *session.Manager
	template     *projectJSON // 新建项目克隆的名册
	replyTimeout time.Duration
	hub          *live.Hub
	fanout       interface{ Stop() }

	mu       sync.Mutex
	path     string
	projects []*projectJSON
	sessions map[string]string
}

// New 装载状态文件；首次启动为每个 spec 建一个默认项目（id = spec 名）。
func New(manager *session.Manager, specs []*swarm.Spec, statePath string, replyTimeout time.Duration, hub *live.Hub) (*Server, error) {
	if hub == nil {
		hub = live.New()
	}
	s := &Server{
		manager:      manager,
		replyTimeout: replyTimeout,
		hub:          hub,
		path:         statePath,
		sessions:     map[string]string{},
	}
	for _, spec := range specs {
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
		ID:   id,
		Name: titleName(spec.Name),
		Goal: spec.Goal,
	}
	for _, a := range spec.Agents {
		p.Agents = append(p.Agents, agentJSON{ID: a.ID, DisplayName: a.DisplayName, Kind: a.Kind})
	}
	return p
}

func (s *Server) ListenAndServe(addr string) error {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /api/health", func(w http.ResponseWriter, r *http.Request) {
		writeJSON(w, 200, map[string]bool{"ok": true})
	})
	mux.HandleFunc("GET /api/projects", s.listProjects)
	mux.HandleFunc("POST /api/projects", s.createProject)
	mux.HandleFunc("DELETE /api/projects/{id}", s.deleteProject)
	mux.HandleFunc("GET /api/projects/{id}/chats", s.listChats)
	mux.HandleFunc("GET /api/projects/{id}/chats/{chat}/messages", s.listMessages)
	mux.HandleFunc("POST /api/projects/{id}/chats/{chat}/messages", s.postMessage)
	mux.HandleFunc("GET /api/projects/{id}/topology", s.topology)
	mux.HandleFunc("GET /api/ws", s.serveWS)
	log.Printf("gateway http://%s", addr)
	return http.ListenAndServe(addr, cors(mux))
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

func (s *Server) createProject(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Name string `json:"name"`
		Goal string `json:"goal"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	name := strings.TrimSpace(body.Name)
	if name == "" {
		writeError(w, 400, "name is required")
		return
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	p := &projectJSON{
		ID:     "p_" + randHex(6),
		Name:   name,
		Goal:   strings.TrimSpace(body.Goal),
		Agents: s.template.Agents,
	}
	if p.Goal == "" {
		p.Goal = s.template.Goal
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
		if p.ID == id {
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
	sessionID, err := s.ensureSession(r.Context(), projectID, key, agentID)
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	userMsg, err := s.manager.AppendMessage(r.Context(), sessionID,
		&sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: userID}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: text}}},
		"", ulid.Make().String(), "")
	if err != nil {
		status := 502
		if errors.Is(err, session.ErrNotYourTurn) {
			status = 409 // agent 还在想：前端禁用输入时不会发生，直发 API 时提示稍候
		}
		writeError(w, status, err.Error())
		return
	}
	agentMsg, err := s.waitReply(r.Context(), sessionID, userMsg.GetMessageId())
	if err != nil {
		writeError(w, 504, "agent 回复超时：worker 在跑但模型太慢，稍后可刷新查看")
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
	deadline := time.Now().Add(s.replyTimeout)
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
