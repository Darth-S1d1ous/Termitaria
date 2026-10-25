package gateway

import (
	"encoding/json"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/oklog/ulid/v2"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
)

// delegateWait 是这条 HTTP 接口自己的等待上限。
// worker 的 delegate 工具不走这里：同事在负责人那一次 task 里跑完，结果作为工具返回。
// 这个接口仍会开一条两人会话并等第一句回复，供测试和外部调用。会话不设轮次上限。
var delegateWait = 25 * time.Minute

// delegate 打开一条两人会话，写入任务，等同事的第一句回复。
// 它不是 worker 里的派活实现。
func (s *Server) delegate(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	var body struct {
		From     string `json:"from"`
		Coworker string `json:"coworker"`
		Task     string `json:"task"`
		Context  string `json:"context"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	fromID := strings.TrimSpace(body.From)
	task := strings.TrimSpace(body.Task)
	who := strings.TrimSpace(body.Coworker)
	if fromID == "" || who == "" || task == "" {
		writeError(w, 400, "from, coworker, and task are required")
		return
	}

	s.mu.Lock()
	var agents []agentJSON
	for _, p := range s.projects {
		if p.ID == projectID {
			agents = append([]agentJSON(nil), p.Agents...)
			break
		}
	}
	s.mu.Unlock()
	if agents == nil {
		writeError(w, 404, "not found")
		return
	}
	if agentByName(agents, fromID) == nil {
		writeError(w, 400, "unknown agent")
		return
	}
	coworker := agentByName(agents, who)
	if coworker == nil {
		writeError(w, 404, "no such coworker")
		return
	}
	if coworker.ID == fromID {
		writeError(w, 400, "cannot delegate to yourself")
		return
	}
	flight := projectID + "/" + coworker.ID
	s.mu.Lock()
	if s.inflight == nil {
		s.inflight = map[string]struct{}{}
	}
	if _, busy := s.inflight[flight]; busy {
		s.mu.Unlock()
		writeError(w, 409, coworker.ID+" is still on the previous task")
		return
	}
	s.inflight[flight] = struct{}{}
	s.mu.Unlock()
	defer func() {
		s.mu.Lock()
		delete(s.inflight, flight)
		s.mu.Unlock()
	}()

	text := task
	if ctx := strings.TrimSpace(body.Context); ctx != "" {
		text += "\n\nContext:\n" + ctx
	}
	sess, err := s.spokeSession(r, projectID, fromID, coworker.ID)
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}

	msg, err := s.manager.AppendMessage(r.Context(), sess.GetSessionId(),
		&sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: fromID}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: text}}},
		"", ulid.Make().String(), "")
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	log.Printf("delegate %s → %s session=%s", fromID, coworker.ID, sess.GetSessionId())
	reply, err := s.waitReplyFor(r.Context(), sess.GetSessionId(), msg.GetMessageId(), delegateWait)
	if err != nil {
		taskID := "t_" + strings.TrimPrefix(msg.GetMessageId(), "m_")
		if cancelErr := s.manager.CancelTask(taskID); cancelErr != nil {
			log.Printf("cancel %s: %v", taskID, cancelErr)
		} else {
			log.Printf("cancel %s（delegate 超时）", taskID)
		}
		writeError(w, 504, "the coworker did not finish before the time limit")
		return
	}
	writeJSON(w, 200, map[string]any{
		"text":       messageText(reply),
		"session_id": sess.GetSessionId(),
		"agent_id":   coworker.ID,
	})
}

// spokeSession 找到这对同事已经开着的会话，没有就新开一条。轮次不设上限。
// worker 的 delegate 工具不调用这个函数。
func (s *Server) spokeSession(r *http.Request, projectID, fromID, coworkerID string) (*sessionv1.Session, error) {
	key := projectID + "/spoke/" + fromID + "/" + coworkerID
	s.mu.Lock()
	sessionID := s.sessions[key]
	s.mu.Unlock()
	if sessionID != "" {
		snap, err := s.manager.Snapshot(r.Context(), sessionID, 0)
		if err == nil && snap.Session.GetState() == sessionv1.SessionState_SESSION_STATE_OPEN {
			return snap.Session, nil
		}
	}
	sess, err := s.manager.OpenSession(r.Context(), projectID,
		[]*sessionv1.Participant{
			{Kind: &sessionv1.Participant_AgentId{AgentId: fromID}},
			{Kind: &sessionv1.Participant_AgentId{AgentId: coworkerID}},
		},
		&sessionv1.SessionPolicy{
			TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT,
		},
		"")
	if err != nil {
		return nil, err
	}
	s.mu.Lock()
	s.sessions[key] = sess.GetSessionId()
	err = s.save()
	s.mu.Unlock()
	if err != nil {
		return nil, err
	}
	return sess, nil
}

// SkipSpokeCaller 报告这条会话里不该再入队的调用方。
// 同事回话之后，轮次会转回调用方；调用方还停在 delegate 的 HTTP 里，再入队就会和这次返回各跑一遍。
func (s *Server) SkipSpokeCaller(sessionID, agentID string) bool {
	if sessionID == "" || agentID == "" {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, id := range s.sessions {
		if id != sessionID {
			continue
		}
		parts := strings.Split(key, "/")
		if len(parts) == 4 && parts[1] == "spoke" && parts[2] == agentID {
			return true
		}
	}
	return false
}

func agentByName(agents []agentJSON, name string) *agentJSON {
	name = strings.TrimSpace(name)
	folded := strings.ToLower(name)
	for i := range agents {
		if agents[i].ID == name || strings.EqualFold(agents[i].ID, name) {
			return &agents[i]
		}
	}
	for i := range agents {
		if strings.EqualFold(agents[i].DisplayName, name) || strings.EqualFold(agents[i].DisplayName, folded) {
			return &agents[i]
		}
	}
	for i := range agents {
		if strings.Contains(strings.ToLower(agents[i].DisplayName), folded) {
			return &agents[i]
		}
	}
	return nil
}
