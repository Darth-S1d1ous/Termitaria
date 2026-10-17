package gateway

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go/jetstream"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/dispatcher"
	"github.com/Darth-S1d1ous/Termitaria/internal/live"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/Darth-S1d1ous/Termitaria/internal/swarm"
	"github.com/Darth-S1d1ous/Termitaria/internal/testutil"
)

type regStart struct {
	reg *dispatcher.Registry
	hub *live.Hub
}

func (s regStart) Start(id, role string, maxTokens uint32) {
	cfg := dispatcher.AgentConfig{
		RolePrompt: role,
		Model:      &commonv1.ModelRef{Provider: "nebius-token-factory", Model: "stub-model"},
		Budget:     &commonv1.Budget{MaxTokens: 512},
	}
	if maxTokens > 0 {
		cfg.Budget.MaxTokens = maxTokens
	}
	if role == "" {
		if prev, ok := s.reg.Lookup(id); ok {
			cfg.RolePrompt = prev.RolePrompt
		}
	}
	s.reg.Register(id, cfg)
	s.hub.SetPresence(id, "online")
}

const fieldSpec = `
name: fieldlab
goal: find a gap
agents:
  - id: pi
    kind: ORCHESTRATOR
    displayName: Principal Investigator
    rolePrompt: You lead.
    budget: { maxTokens: 1000 }
  - id: collector
    displayName: Literature Collector
    rolePrompt: You sweep.
  - id: reviewer
    displayName: Skeptical Reviewer
    rolePrompt: You attack.
`

func TestCreateFromSpecPullsAgentsAndRoutesChats(t *testing.T) {
	b := testutil.NewBus(t)
	manager := session.NewManager(b)
	reg := dispatcher.NewRegistry()
	hub := live.New()
	d := dispatcher.New(b, manager, reg, 50)
	d.SetHub(hub)
	if err := d.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(d.Stop)

	boot, err := swarm.Parse([]byte(fieldSpec))
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(manager, []*swarm.Spec{boot}, filepath.Join(t.TempDir(), "state.json"), time.Second, hub, regStart{reg, hub})
	if err != nil {
		t.Fatal(err)
	}
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)

	res := postJSON(t, httpSrv.URL+"/api/projects", map[string]string{
		"name": "Field Lab",
		"spec": fieldSpec,
	})
	if res.StatusCode != 201 {
		t.Fatalf("create status = %d", res.StatusCode)
	}
	var created projectJSON
	if err := json.NewDecoder(res.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if created.Name != "Field Lab" || created.Goal != "find a gap" || len(created.Agents) != 3 {
		t.Fatalf("project = %+v", created)
	}
	if len(created.Steps) != 1 || created.Steps[0].Label != "find a gap" || created.Steps[0].State != "active" {
		t.Fatalf("steps = %+v", created.Steps)
	}
	if !orchestratorToldUser(t, manager, created.ID, "find a gap") {
		t.Fatal("orchestrator did not announce the plan in the user session")
	}
	for _, id := range []string{"pi", "collector", "reviewer"} {
		if hub.State(id) != "online" {
			t.Fatalf("%s presence = %s", id, hub.State(id))
		}
		if _, ok := reg.Lookup(id); !ok {
			t.Fatalf("%s was not registered", id)
		}
	}

	chatRes, err := http.Get(httpSrv.URL + "/api/projects/" + created.ID + "/chats")
	if err != nil {
		t.Fatal(err)
	}
	defer chatRes.Body.Close()
	var chats struct {
		Chats []chatJSON `json:"chats"`
	}
	if err := json.NewDecoder(chatRes.Body).Decode(&chats); err != nil {
		t.Fatal(err)
	}
	kinds := map[string]string{}
	for _, chat := range chats.Chats {
		kinds[chat.ID] = chat.Kind
	}
	if kinds[channelChatID] != "channel" || kinds["collector"] != "agent" || kinds["reviewer"] != "agent" || kinds["pi"] != "agent" {
		t.Fatalf("chats = %+v", kinds)
	}

	dm := speak(t, srv, created.ID, "collector", "")
	ch := speak(t, srv, created.ID, channelChatID, "")
	mentioned := speak(t, srv, created.ID, channelChatID, "reviewer")
	if dm.agentID != "collector" || ch.agentID != "pi" || mentioned.agentID != "reviewer" {
		t.Fatalf("routes dm=%s channel=%s mention=%s", dm.agentID, ch.agentID, mentioned.agentID)
	}
	if dm.sessionID == ch.sessionID {
		t.Fatal("private chat and group chat collapsed onto one session")
	}

	if task, ok := fetchTask(t, b, created.ID, "collector"); !ok || task.GetAgentId() != "collector" {
		t.Fatalf("private chat task = %+v ok=%v", task, ok)
	}
	if task, ok := fetchTask(t, b, created.ID, "pi"); !ok || task.GetAgentId() != "pi" {
		t.Fatalf("group chat task = %+v ok=%v", task, ok)
	}
	if task, ok := fetchTask(t, b, created.ID, "reviewer"); !ok || task.GetAgentId() != "reviewer" {
		t.Fatalf("mention task = %+v ok=%v", task, ok)
	}

	bad := postJSON(t, httpSrv.URL+"/api/projects", map[string]string{"name": "nope", "spec": "name: x\n"})
	if bad.StatusCode != 400 {
		t.Fatalf("bad spec status = %d", bad.StatusCode)
	}
	bad.Body.Close()
}

type spoken struct {
	agentID   string
	sessionID string
}

func orchestratorToldUser(t *testing.T, m *session.Manager, projectID, label string) bool {
	t.Helper()
	list, err := m.ListSessions(context.Background(), projectID, sessionv1.SessionState_SESSION_STATE_UNSPECIFIED)
	if err != nil {
		t.Fatal(err)
	}
	for _, sess := range list {
		snap, err := m.Snapshot(context.Background(), sess.GetSessionId(), 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, msg := range snap.Window {
			if msg.GetFrom().GetAgentId() == "pi" && strings.Contains(messageText(msg), label) {
				return true
			}
		}
	}
	return false
}

func speak(t *testing.T, s *Server, projectID, chatID, mention string) spoken {
	t.Helper()
	agentID, key, err := s.sendTarget(projectID, chatID, mention)
	if err != nil {
		t.Fatal(err)
	}
	sessionID, err := s.ensureSession(context.Background(), projectID, key, agentID)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.manager.AppendMessage(context.Background(), sessionID,
		&sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: userID}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: "hello"}}},
		"", "", "")
	if err != nil {
		t.Fatal(err)
	}
	return spoken{agentID: agentID, sessionID: sessionID}
}

func postJSON(t *testing.T, url string, body any) *http.Response {
	t.Helper()
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	res, err := http.Post(url, "application/json", bytes.NewReader(raw))
	if err != nil {
		t.Fatal(err)
	}
	return res
}

func fetchTask(t *testing.T, b *bus.Bus, swarmID, agentID string) (*taskv1.Task, bool) {
	t.Helper()
	cons, err := b.JetStream().CreateOrUpdateConsumer(context.Background(), "TASKS", jetstream.ConsumerConfig{
		Durable:       "gw-fetch-" + agentID,
		FilterSubject: bus.TaskQueue(swarmID, agentID),
		DeliverPolicy: jetstream.DeliverAllPolicy,
		AckPolicy:     jetstream.AckExplicitPolicy,
	})
	if err != nil {
		t.Fatalf("task consumer: %v", err)
	}
	batch, err := cons.Fetch(1, jetstream.FetchMaxWait(2*time.Second))
	if err != nil {
		t.Fatalf("fetch: %v", err)
	}
	for msg := range batch.Messages() {
		var task taskv1.Task
		if _, err := bus.Unmarshal(msg.Data(), &task); err != nil {
			t.Fatal(err)
		}
		_ = msg.Ack()
		return &task, true
	}
	return nil, false
}
