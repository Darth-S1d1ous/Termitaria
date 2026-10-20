package gateway

import (
	"context"
	"encoding/json"
	"net/http"
	"strings"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/nats-io/nats.go"
	"github.com/oklog/ulid/v2"

	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
)

type stepJSON struct {
	ID    string `json:"id"`
	Label string `json:"label"`
	State string `json:"state"` // pending | active | done | failed
}

// planLabels 从项目目标拆出进度节点。有箭头时用箭头后面的阶段，否则按句子切。
func planLabels(goal string) []string {
	goal = strings.TrimSpace(goal)
	raw := goal
	if strings.Contains(goal, "→") {
		if i := strings.LastIndex(goal, "\n"); i >= 0 && strings.Contains(goal[i:], "→") {
			raw = goal[i+1:]
		} else if i := strings.LastIndex(goal, "："); i >= 0 && strings.Contains(goal[i:], "→") {
			raw = goal[i+len("："):]
		}
	}
	raw = strings.NewReplacer("→", "\n", "->", "\n", "；", "\n", ";", "\n", "。", "\n").Replace(raw)
	var out []string
	for _, line := range strings.Split(raw, "\n") {
		line = strings.Trim(strings.TrimSpace(line), "，,、:： ")
		if line == "" {
			continue
		}
		out = append(out, line)
		if len(out) == 6 {
			break
		}
	}
	if len(out) == 0 {
		return []string{"Start"}
	}
	return out
}

func newPlan(goal string) []stepJSON {
	labels := planLabels(goal)
	steps := make([]stepJSON, 0, len(labels))
	for i, label := range labels {
		state := "pending"
		if i == 0 {
			state = "active"
		}
		steps = append(steps, stepJSON{ID: "s" + itoa(i+1), Label: label, State: state})
	}
	return steps
}

func planText(steps []stepJSON) string {
	labels := make([]string, 0, len(steps))
	for _, step := range steps {
		labels = append(labels, step.Label)
	}
	return "I split the goal into these steps: " + strings.Join(labels, " → ") + ". The first step is " + steps[0].Label + "."
}

func orchestratorID(p *projectJSON) string {
	for _, agent := range p.Agents {
		if agent.Kind == "ORCHESTRATOR" {
			return agent.ID
		}
	}
	if len(p.Agents) > 0 {
		return p.Agents[0].ID
	}
	return ""
}

// announcePlan 以 orchestrator 的身份把计划写进它和用户的 session。
// 参与者顺序是 orchestrator、user，所以第一条发言是 orchestrator，下一轮才轮到用户。
func (s *Server) announcePlan(ctx context.Context, p *projectJSON) error {
	orch := orchestratorID(p)
	if orch == "" || len(p.Steps) == 0 {
		return nil
	}
	key := sessionKey(p.ID, orch, orch)
	sess, err := s.manager.OpenSession(ctx, p.ID,
		[]*sessionv1.Participant{
			{Kind: &sessionv1.Participant_AgentId{AgentId: orch}},
			{Kind: &sessionv1.Participant_UserId{UserId: userID}},
		},
		&sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT},
		"")
	if err != nil {
		return err
	}
	s.sessions[key] = sess.GetSessionId()
	_, err = s.manager.AppendMessage(ctx, sess.GetSessionId(),
		&sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: orch}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: planText(p.Steps)}}},
		"", ulid.Make().String(), "")
	return err
}

// updateStep 让模型把进度条往前推。label 可以是步骤 id，也可以是标签里的一段文字。
func (s *Server) updateStep(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	var body struct {
		Label string `json:"label"`
		State string `json:"state"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeError(w, 400, "bad json")
		return
	}
	state := strings.TrimSpace(body.State)
	switch state {
	case "pending", "active", "done", "failed":
	default:
		writeError(w, 400, "state must be pending, active, done, or failed")
		return
	}
	s.mu.Lock()
	var steps []stepJSON
	for _, p := range s.projects {
		if p.ID != projectID {
			continue
		}
		idx := matchStep(p.Steps, body.Label)
		if idx < 0 {
			s.mu.Unlock()
			writeError(w, 404, "no such step")
			return
		}
		p.Steps[idx].State = state
		if state == "done" {
			busy := false
			for _, step := range p.Steps {
				if step.State == "active" {
					busy = true
					break
				}
			}
			if !busy {
				for i := range p.Steps {
					if p.Steps[i].State == "pending" {
						p.Steps[i].State = "active"
						break
					}
				}
			}
		}
		steps = p.Steps
		if err := s.save(); err != nil {
			s.mu.Unlock()
			writeError(w, 502, err.Error())
			return
		}
		break
	}
	s.mu.Unlock()
	if steps == nil {
		writeError(w, 404, "not found")
		return
	}
	s.broadcast(projectID, map[string]any{"type": "plan", "steps": steps})
	writeJSON(w, 200, map[string]any{"steps": steps})
}

func matchStep(steps []stepJSON, label string) int {
	label = strings.TrimSpace(label)
	if label == "" {
		return -1
	}
	for i, step := range steps {
		if step.ID == label || step.Label == label {
			return i
		}
	}
	for i, step := range steps {
		if strings.Contains(step.Label, label) || strings.Contains(label, step.Label) {
			return i
		}
	}
	return -1
}

func (s *Server) failActive(projectID string) {
	if projectID == "" {
		return
	}
	s.mu.Lock()
	var steps []stepJSON
	for _, p := range s.projects {
		if p.ID != projectID {
			continue
		}
		for i := range p.Steps {
			if p.Steps[i].State == "active" {
				p.Steps[i].State = "failed"
				steps = p.Steps
				break
			}
		}
	}
	if steps != nil {
		_ = s.save()
	}
	s.mu.Unlock()
	if steps != nil {
		s.broadcast(projectID, map[string]any{"type": "plan", "steps": steps})
	}
}

func (s *Server) onTaskResult(msg *nats.Msg) {
	var result taskv1.TaskResult
	if _, err := bus.Unmarshal(msg.Data, &result); err != nil {
		return
	}
	if !budgetOrTimeout(result.GetStatus(), result.GetError().GetCode()) {
		return
	}
	projectID := ""
	if m := result.GetMessage(); m != nil {
		projectID = s.projectOfSession(m.GetSessionId())
	}
	if projectID == "" && s.hub != nil {
		if meta, ok := s.hub.Task(result.GetTaskId()); ok {
			projectID = meta.SwarmID
		}
	}
	s.failActive(projectID)
}

func (s *Server) projectOfSession(sessionID string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	for key, id := range s.sessions {
		if id != sessionID {
			continue
		}
		project, _, _ := strings.Cut(key, "/")
		return project
	}
	return ""
}

func budgetOrTimeout(status taskv1.TaskStatus, code string) bool {
	if status == taskv1.TaskStatus_TASK_STATUS_CANCELLED {
		return true
	}
	if status != taskv1.TaskStatus_TASK_STATUS_FAILED {
		return false
	}
	code = strings.ToUpper(code)
	return strings.Contains(code, "BUDGET") || strings.Contains(code, "TIMEOUT")
}

func itoa(n int) string {
	if n == 0 {
		return "0"
	}
	var buf [8]byte
	i := len(buf)
	for n > 0 {
		i--
		buf[i] = byte('0' + n%10)
		n /= 10
	}
	return string(buf[i:])
}
