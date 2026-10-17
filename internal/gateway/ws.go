package gateway

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strings"
	"time"

	"github.com/coder/websocket"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/live"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
)

// Start 订阅 delta、presence 和 session 事件，转成 WebSocket 帧。
func (s *Server) Start(ctx context.Context, b *bus.Bus) error {
	if _, err := b.Conn().Subscribe(bus.TaskDeltaAll, s.onDelta); err != nil {
		return err
	}
	if _, err := b.Conn().Subscribe(bus.PresenceAll, s.onPresence); err != nil {
		return err
	}
	if _, err := b.Conn().Subscribe(bus.TaskResultAll, s.onTaskResult); err != nil {
		return err
	}
	cons, err := b.JetStream().CreateOrUpdateConsumer(ctx, "SESSIONS", jetstream.ConsumerConfig{
		Durable:       "gateway-fanout",
		FilterSubject: bus.SessionEventsAll,
		DeliverPolicy: jetstream.DeliverNewPolicy,
		AckPolicy:     jetstream.AckExplicitPolicy,
	})
	if err != nil {
		return err
	}
	cc, err := cons.Consume(s.onSessionEvent)
	if err != nil {
		return err
	}
	s.fanout = cc
	return nil
}

func (s *Server) onPresence(msg *nats.Msg) {
	agentID := strings.TrimPrefix(msg.Subject, "presence.")
	if agentID == "" || agentID == msg.Subject {
		return
	}
	s.hub.SetPresence(agentID, string(msg.Data))
}

func (s *Server) onDelta(msg *nats.Msg) {
	var delta taskv1.TaskDelta
	if _, err := bus.Unmarshal(msg.Data, &delta); err != nil || delta.GetText() == "" {
		return
	}
	meta, ok := s.hub.Task(delta.GetTaskId())
	if !ok {
		return
	}
	raw, err := json.Marshal(map[string]any{
		"type":       "message.delta",
		"session_id": meta.SessionID,
		"message_id": live.MessageIDForTask(delta.GetTaskId()),
		"seq":        delta.GetSeq(),
		"text":       delta.GetText(),
		"agent_id":   meta.AgentID,
		"chat_id":    s.chatOf(meta.SwarmID, meta.SessionID),
	})
	if err != nil {
		return
	}
	s.hub.Broadcast(meta.SwarmID, raw)
}

func (s *Server) onSessionEvent(msg jetstream.Msg) {
	var ev sessionv1.SessionEvent
	if _, err := bus.Unmarshal(msg.Data(), &ev); err != nil {
		_ = msg.Ack()
		return
	}
	defer func() { _ = msg.Ack() }()

	switch {
	case ev.GetOpened() != nil:
		sess := ev.GetOpened().GetSession()
		from, to, ok := agentPair(sess)
		if !ok {
			return
		}
		s.broadcast(ev.GetSwarmId(), map[string]any{
			"type":       "topology.session_opened",
			"swarm_id":   ev.GetSwarmId(),
			"session_id": sess.GetSessionId(),
			"from":       from,
			"to":         to,
		})
	case ev.GetClosed() != nil:
		s.broadcast(ev.GetSwarmId(), map[string]any{
			"type":       "topology.session_closed",
			"swarm_id":   ev.GetSwarmId(),
			"session_id": ev.GetSessionId(),
		})
	case ev.GetMessageAppended() != nil:
		message := ev.GetMessageAppended().GetMessage()
		s.broadcast(ev.GetSwarmId(), map[string]any{
			"type":       "message.final",
			"session_id": ev.GetSessionId(),
			"chat_id":    s.chatOf(ev.GetSwarmId(), ev.GetSessionId()),
			"message":    s.messageJSON(ev.GetSwarmId(), message),
		})
	}
}

func (s *Server) broadcast(swarmID string, payload any) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return
	}
	s.hub.Broadcast(swarmID, raw)
}

func (s *Server) chatOf(projectID, sessionID string) string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return chatIDForSession(projectID, sessionID, s.sessions)
}

type edgeJSON struct {
	ID   string `json:"id"`
	From string `json:"from"`
	To   string `json:"to"`
}

func (s *Server) topology(w http.ResponseWriter, r *http.Request) {
	projectID := r.PathValue("id")
	project := s.project(projectID)
	if project == nil {
		writeError(w, 404, "not found")
		return
	}
	edges := []edgeJSON{}
	list, err := s.manager.ListSessions(r.Context(), projectID, sessionv1.SessionState_SESSION_STATE_OPEN)
	if err != nil {
		writeError(w, 502, err.Error())
		return
	}
	for _, sess := range list {
		from, to, ok := agentPair(sess)
		if !ok {
			continue
		}
		edges = append(edges, edgeJSON{ID: sess.GetSessionId(), From: from, To: to})
	}
	agents := make([]map[string]string, 0, len(project.Agents))
	for _, agent := range project.Agents {
		agents = append(agents, map[string]string{
			"id":           agent.ID,
			"display_name": agent.DisplayName,
			"presence":     s.hub.State(agent.ID),
		})
	}
	writeJSON(w, 200, map[string]any{"agents": agents, "sessions": edges})
}

func agentPair(sess *sessionv1.Session) (string, string, bool) {
	if !bothAgents(sess) {
		return "", "", false
	}
	ps := sess.GetParticipants()
	return ps[0].GetAgentId(), ps[1].GetAgentId(), true
}

func (s *Server) serveWS(w http.ResponseWriter, r *http.Request) {
	projectID := r.URL.Query().Get("project")
	project := s.project(projectID)
	if project == nil {
		writeError(w, 404, "not found")
		return
	}
	conn, err := websocket.Accept(w, r, &websocket.AcceptOptions{InsecureSkipVerify: true})
	if err != nil {
		log.Printf("ws accept: %v", err)
		return
	}
	defer conn.Close(websocket.StatusNormalClosure, "")

	ctx, cancel := context.WithCancel(r.Context())
	defer cancel()
	events := s.hub.Subscribe(projectID)
	defer s.hub.Unsubscribe(events)

	go func() {
		for raw := range events {
			writeCtx, cancelWrite := context.WithTimeout(ctx, 5*time.Second)
			err := conn.Write(writeCtx, websocket.MessageText, raw)
			cancelWrite()
			if err != nil {
				cancel()
				return
			}
		}
	}()

	for _, agent := range project.Agents {
		push(events, map[string]string{
			"type": "presence", "agent_id": agent.ID, "state": s.hub.State(agent.ID),
		})
	}

	for {
		_, data, err := conn.Read(ctx)
		if err != nil {
			return
		}
		s.onClient(ctx, projectID, data, events)
	}
}

type clientFrame struct {
	Type           string `json:"type"`
	ChatID         string `json:"chat_id"`
	MentionAgentID string `json:"mention_agent_id"`
	ClientMsgID    string `json:"client_msg_id"`
	Text           string `json:"text"`
}

func (s *Server) onClient(ctx context.Context, projectID string, data []byte, events chan []byte) {
	var frame clientFrame
	if err := json.Unmarshal(data, &frame); err != nil || frame.Type != "message.send" {
		return
	}
	text := strings.TrimSpace(frame.Text)
	if text == "" {
		push(events, map[string]string{
			"type": "error", "chat_id": frame.ChatID, "client_msg_id": frame.ClientMsgID,
			"code": "BAD_REQUEST", "message": "text is required",
		})
		return
	}
	agentID, key, err := s.sendTarget(projectID, frame.ChatID, frame.MentionAgentID)
	if err != nil {
		push(events, map[string]string{
			"type": "error", "chat_id": frame.ChatID, "client_msg_id": frame.ClientMsgID,
			"code": "BAD_REQUEST", "message": err.Error(),
		})
		return
	}
	sessionID, err := s.ensureSession(ctx, projectID, key, agentID)
	if err != nil {
		push(events, map[string]string{
			"type": "error", "chat_id": frame.ChatID, "client_msg_id": frame.ClientMsgID,
			"code": "UNAVAILABLE", "message": err.Error(),
		})
		return
	}
	_, err = s.manager.AppendMessage(ctx, sessionID,
		&sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: userID}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: text}}},
		"", frame.ClientMsgID, "")
	if err != nil {
		code := "UNAVAILABLE"
		if errors.Is(err, session.ErrNotYourTurn) {
			code = "POLICY_DENIED"
		}
		push(events, map[string]string{
			"type": "error", "chat_id": frame.ChatID, "client_msg_id": frame.ClientMsgID,
			"code": code, "message": err.Error(),
		})
	}
}

func push(ch chan []byte, payload any) {
	raw, err := json.Marshal(payload)
	if err != nil {
		return
	}
	select {
	case ch <- raw:
	default:
	}
}
