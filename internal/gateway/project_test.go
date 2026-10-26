package gateway

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
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
	srv, err := New(manager, []*swarm.Spec{boot}, filepath.Join(t.TempDir(), "state.json"), time.Second, hub, regStart{reg, hub}, "")
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

func TestUpdateStepMarksDone(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	created := createField(t, httpSrv.URL)
	res := postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/steps", map[string]string{
		"label": "find a gap",
		"state": "done",
	})
	if res.StatusCode != 409 {
		t.Fatalf("status = %d", res.StatusCode)
	}
	res.Body.Close()
	postArtifact(t, httpSrv.URL, created.ID, "brief.md", "report", []byte("# Brief\n\n"+strings.Repeat("Copper moved lower on the seed note. ", 8)))
	postArtifact(t, httpSrv.URL, created.ID, "brief.pptx", "deck", bytes.Repeat([]byte("P"), 800))
	res = postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/steps", map[string]string{
		"label": "find a gap",
		"state": "done",
	})
	if res.StatusCode != 200 {
		body, _ := io.ReadAll(res.Body)
		t.Fatalf("status = %d %s", res.StatusCode, body)
	}
	res.Body.Close()
	got := projectByID(t, httpSrv.URL, created.ID)
	if len(got.Steps) != 1 || got.Steps[0].State != "done" {
		t.Fatalf("steps = %+v", got.Steps)
	}
	if len(got.Artifacts) != 2 {
		t.Fatalf("artifacts = %+v", got.Artifacts)
	}
	file, err := http.Get(httpSrv.URL + "/api/projects/" + created.ID + "/artifacts/brief.md")
	if err != nil {
		t.Fatal(err)
	}
	defer file.Body.Close()
	if file.StatusCode != 200 {
		t.Fatalf("download = %d", file.StatusCode)
	}
}

func TestCreateKeepsSeedsAndRejectsUnknownSkill(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	res := postJSON(t, httpSrv.URL+"/api/projects", map[string]any{
		"name": "Copper Squeeze Desk",
		"spec": fieldSpec,
		"seeds": []map[string]string{{
			"kind": "text", "title": "LME", "body": "LME copper closed at 9840.",
		}},
	})
	if res.StatusCode != 201 {
		t.Fatalf("create status = %d", res.StatusCode)
	}
	var created projectJSON
	if err := json.NewDecoder(res.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if len(created.Seeds) != 1 || created.Seeds[0].Body != "LME copper closed at 9840." {
		t.Fatalf("seeds = %+v", created.Seeds)
	}
	dir := t.TempDir()
	t.Setenv("SKILLS_DIR", dir)
	if err := os.WriteFile(filepath.Join(dir, "market-data.md"), []byte("---\nname: market-data\n---\nprices\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	bad := postJSON(t, httpSrv.URL+"/api/projects", map[string]string{
		"name": "Nope",
		"spec": fieldSpec + "    skills: [not-a-skill]\n",
	})
	if bad.StatusCode != 400 {
		t.Fatalf("unknown skill status = %d", bad.StatusCode)
	}
	bad.Body.Close()
}

func TestReportRubricRejectsAMissingSection(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	spec := "name: paperlab\ngoal: write the paper\ndeliverable:\n  kind: paper\nagents:\n  - id: pi\n    kind: ORCHESTRATOR\n    rolePrompt: lead\n  - id: hand\n    rolePrompt: hand\n"
	res := postJSON(t, httpSrv.URL+"/api/projects", map[string]string{"name": "Paper", "spec": spec})
	if res.StatusCode != 201 {
		body, _ := io.ReadAll(res.Body)
		t.Fatalf("create status = %d %s", res.StatusCode, body)
	}
	var created projectJSON
	if err := json.NewDecoder(res.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if created.Deliverable == nil || created.Deliverable.Kind != "paper" || created.Deliverable.RequireDeck {
		t.Fatalf("deliverable = %+v", created.Deliverable)
	}
	res = postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/artifacts", map[string]string{
		"name": "paper.md", "kind": "report",
		"content_base64": base64.StdEncoding.EncodeToString([]byte("## Abstract\n\n" + strings.Repeat("A claim without a source. ", 40))),
	})
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if res.StatusCode != 400 || !strings.Contains(string(body), "Related work") {
		t.Fatalf("status = %d %s", res.StatusCode, body)
	}
}

func postArtifact(t *testing.T, base, projectID, name, kind string, raw []byte) {
	t.Helper()
	res := postJSON(t, base+"/api/projects/"+projectID+"/artifacts", map[string]string{
		"name": name, "kind": kind, "content_base64": base64.StdEncoding.EncodeToString(raw),
	})
	if res.StatusCode != 201 {
		body, _ := io.ReadAll(res.Body)
		t.Fatalf("artifact %s status = %d %s", name, res.StatusCode, body)
	}
	res.Body.Close()
}

func TestFileArtifactIsDownloadable(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	created := createField(t, httpSrv.URL)
	raw := []byte("# Protocol v5\n\nDesign of record.\n")
	postArtifact(t, httpSrv.URL, created.ID, "protocol_v5.md", "file", raw)
	res, err := http.Get(httpSrv.URL + "/api/projects/" + created.ID + "/artifacts/protocol_v5.md")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(res.Body)
	res.Body.Close()
	if res.StatusCode != 200 || string(body) != string(raw) {
		t.Fatalf("status = %d body = %q", res.StatusCode, body)
	}
}

func TestUploadKnowledgeWhileTheProjectExists(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	created := createField(t, httpSrv.URL)
	res := postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/knowledge", map[string]string{
		"title": "notes.md",
		"text":  "Copper closed at 9840.",
	})
	if res.StatusCode != 201 {
		body, _ := io.ReadAll(res.Body)
		t.Fatalf("upload status = %d %s", res.StatusCode, body)
	}
	var saved struct {
		ID     string `json:"id"`
		NodeID string `json:"node_id"`
		Title  string `json:"title"`
	}
	if err := json.NewDecoder(res.Body).Decode(&saved); err != nil {
		t.Fatal(err)
	}
	res.Body.Close()
	if !strings.HasPrefix(saved.ID, "u_") || saved.Title != "notes.md" || saved.NodeID != created.ID+"/"+saved.ID {
		t.Fatalf("saved = %+v", saved)
	}
	project := projectByID(t, httpSrv.URL, created.ID)
	if len(project.Seeds) != 1 || project.Seeds[0].Body != "Copper closed at 9840." || project.Seeds[0].ID != saved.ID {
		t.Fatalf("seeds = %+v", project.Seeds)
	}
	empty := postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/knowledge", map[string]string{"title": "empty.md", "text": "  "})
	if empty.StatusCode != 400 {
		t.Fatalf("empty status = %d", empty.StatusCode)
	}
	empty.Body.Close()
	missing := postJSON(t, httpSrv.URL+"/api/projects/missing/knowledge", map[string]string{"title": "a.md", "text": "hello"})
	if missing.StatusCode != 404 {
		t.Fatalf("missing status = %d", missing.StatusCode)
	}
	missing.Body.Close()
}

func TestReplyTimeoutLeavesTheStepActive(t *testing.T) {
	srv, _ := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	created := createField(t, httpSrv.URL)
	res := postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/chats/pi/messages", map[string]string{
		"text": "开始扫文献",
	})
	if res.StatusCode != 504 {
		body, _ := io.ReadAll(res.Body)
		t.Fatalf("status = %d %s", res.StatusCode, body)
	}
	res.Body.Close()
	project := projectByID(t, httpSrv.URL, created.ID)
	if len(project.Steps) != 1 || project.Steps[0].State != "active" {
		t.Fatalf("steps = %+v", project.Steps)
	}
}

func TestDelegateOpensSessionThenTimesOut(t *testing.T) {
	old := delegateWait
	delegateWait = 200 * time.Millisecond
	t.Cleanup(func() { delegateWait = old })
	srv, manager := testServer(t)
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	created := createField(t, httpSrv.URL)
	res := postJSON(t, httpSrv.URL+"/api/projects/"+created.ID+"/delegate", map[string]string{
		"from": "pi", "coworker": "collector", "task": "去把文献过一遍", "context": "没有现成材料",
	})
	if res.StatusCode != 504 {
		t.Fatalf("status = %d", res.StatusCode)
	}
	res.Body.Close()
	list, err := manager.ListSessions(context.Background(), created.ID, sessionv1.SessionState_SESSION_STATE_UNSPECIFIED)
	if err != nil {
		t.Fatal(err)
	}
	var saw bool
	for _, sess := range list {
		snap, err := manager.Snapshot(context.Background(), sess.GetSessionId(), 0)
		if err != nil {
			t.Fatal(err)
		}
		for _, msg := range snap.Window {
			if msg.GetFrom().GetAgentId() == "pi" && strings.Contains(messageText(msg), "文献过一遍") {
				saw = true
			}
		}
	}
	if !saw {
		t.Fatal("delegate did not open a spoke")
	}
	for _, sess := range list {
		if sess.GetPolicy().GetMaxTurns() != 0 {
			t.Fatalf("max turns = %d, want unlimited", sess.GetPolicy().GetMaxTurns())
		}
		if sess.GetState() != sessionv1.SessionState_SESSION_STATE_OPEN {
			t.Fatalf("state = %s, want open", sess.GetState())
		}
	}
}

func TestSpecsReloadWithoutRestart(t *testing.T) {
	dir := t.TempDir()
	if err := os.WriteFile(filepath.Join(dir, "a.yaml"), []byte(miniSpec("alpha")), 0o644); err != nil {
		t.Fatal(err)
	}
	loaded, err := swarm.LoadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	b := testutil.NewBus(t)
	manager := session.NewManager(b)
	reg := dispatcher.NewRegistry()
	hub := live.New()
	srv, err := New(manager, loaded, filepath.Join(t.TempDir(), "state.json"), time.Second, hub, regStart{reg, hub}, dir)
	if err != nil {
		t.Fatal(err)
	}
	httpSrv := httptest.NewServer(srv.Handler())
	t.Cleanup(httpSrv.Close)
	if !specListed(t, httpSrv.URL, "alpha") {
		t.Fatal("alpha missing")
	}
	if err := os.WriteFile(filepath.Join(dir, "b.yaml"), []byte(miniSpec("beta")), 0o644); err != nil {
		t.Fatal(err)
	}
	if !specListed(t, httpSrv.URL, "beta") {
		t.Fatal("beta missing after reload")
	}
}

func testServer(t *testing.T) (*Server, *session.Manager) {
	t.Helper()
	b := testutil.NewBus(t)
	manager := session.NewManager(b)
	reg := dispatcher.NewRegistry()
	hub := live.New()
	boot, err := swarm.Parse([]byte(fieldSpec))
	if err != nil {
		t.Fatal(err)
	}
	srv, err := New(manager, []*swarm.Spec{boot}, filepath.Join(t.TempDir(), "state.json"), time.Second, hub, regStart{reg, hub}, "")
	if err != nil {
		t.Fatal(err)
	}
	return srv, manager
}

func createField(t *testing.T, base string) projectJSON {
	t.Helper()
	res := postJSON(t, base+"/api/projects", map[string]string{"name": "Field Lab", "spec": fieldSpec})
	if res.StatusCode != 201 {
		t.Fatalf("create status = %d", res.StatusCode)
	}
	defer res.Body.Close()
	var created projectJSON
	if err := json.NewDecoder(res.Body).Decode(&created); err != nil {
		t.Fatal(err)
	}
	return created
}

func projectByID(t *testing.T, base, id string) projectJSON {
	t.Helper()
	res, err := http.Get(base + "/api/projects")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var body struct {
		Projects []projectJSON `json:"projects"`
	}
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	for _, project := range body.Projects {
		if project.ID == id {
			return project
		}
	}
	t.Fatalf("project %s missing", id)
	return projectJSON{}
}

func specListed(t *testing.T, base, name string) bool {
	t.Helper()
	res, err := http.Get(base + "/api/swarmspecs")
	if err != nil {
		t.Fatal(err)
	}
	defer res.Body.Close()
	var body struct {
		Specs []specJSON `json:"specs"`
	}
	if err := json.NewDecoder(res.Body).Decode(&body); err != nil {
		t.Fatal(err)
	}
	for _, spec := range body.Specs {
		if spec.Name == name {
			return true
		}
	}
	return false
}

func miniSpec(name string) string {
	return "name: " + name + "\ngoal: ship it\nagents:\n  - id: lead\n    kind: ORCHESTRATOR\n    rolePrompt: lead\n  - id: hand\n    rolePrompt: hand\n"
}
