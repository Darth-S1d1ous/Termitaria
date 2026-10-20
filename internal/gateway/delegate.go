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

// delegateWait 要盖住同事自己的工具循环。一轮模型调用要几分钟，六轮加上排队会超过一刻钟。
// 到点就取消任务，不让它继续占着接口，也不要把晚到的结果丢给已经往下走的负责人。
var delegateWait = 25 * time.Minute

// delegate 是 CrewAI「Delegate work to coworker」在这条总线上的做法：
// 开一条两人会话，把任务写成第一条消息，等同事回一句，把正文交还给调用方。
// 调用方还停在自己的那一轮里，所以用户不用先说一句才能往下走。
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
	// 一轮交代、一轮交卷。交卷后会话挂起，负责人不会在这条支线上被再次叫醒。
	sess, err := s.manager.OpenSession(r.Context(), projectID,
		[]*sessionv1.Participant{
			{Kind: &sessionv1.Participant_AgentId{AgentId: fromID}},
			{Kind: &sessionv1.Participant_AgentId{AgentId: coworker.ID}},
		},
		&sessionv1.SessionPolicy{
			TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT,
			MaxTurns:   2,
		},
		"")
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	s.mu.Lock()
	s.sessions[projectID+"/spoke/"+fromID+"/"+coworker.ID+"/"+sess.GetSessionId()] = sess.GetSessionId()
	err = s.save()
	s.mu.Unlock()
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
