package dispatcher

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/nats-io/nats.go/jetstream"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/Darth-S1d1ous/Termitaria/internal/testutil"
)

func agent(id string) *sessionv1.Participant {
	return &sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: id}}
}

func user(id string) *sessionv1.Participant {
	return &sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: id}}
}

func text(s string) *sessionv1.ContentBlock {
	return &sessionv1.ContentBlock{Block: &sessionv1.ContentBlock_Text{Text: s}}
}

func strictPolicy() *sessionv1.SessionPolicy {
	return &sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT}
}

var stubConfig = AgentConfig{
	Model: &commonv1.ModelRef{
		Provider: "nebius-token-factory",
		Tier:     commonv1.ModelTier_MODEL_TIER_SUPER,
		Model:    "stub-model",
	},
	Budget: &commonv1.Budget{MaxTokens: 512},
}

func newDispatcher(t *testing.T) (*bus.Bus, *session.Manager, *Dispatcher) {
	t.Helper()
	b := testutil.NewBus(t)
	m := session.NewManager(b)
	d := New(b, m, StaticRegistry{"ideator": stubConfig, "reviewer": stubConfig}, 50)
	return b, m, d
}

func start(t *testing.T, d *Dispatcher) {
	t.Helper()
	if err := d.Start(context.Background()); err != nil {
		t.Fatalf("dispatcher start: %v", err)
	}
	t.Cleanup(d.Stop)
}

func appendMsg(t *testing.T, m *session.Manager, sessionID string, from *sessionv1.Participant) *sessionv1.Message {
	t.Helper()
	msg, err := m.AppendMessage(context.Background(), sessionID, from,
		[]*sessionv1.ContentBlock{text("hello")}, "", "", "")
	if err != nil {
		t.Fatalf("append: %v", err)
	}
	return msg
}

// fetchTask 从 TASKS 工作队列取一条 task——测试即「假 worker」。
// durable 固定，重复调用复用同一 consumer（WorkQueue 不允许 filter 重叠的并存 consumer）。
func fetchTask(t *testing.T, b *bus.Bus, swarm, agentID string) (*taskv1.Task, bool) {
	t.Helper()
	cons, err := b.JetStream().CreateOrUpdateConsumer(context.Background(), "TASKS", jetstream.ConsumerConfig{
		Durable:       "test-fetch-" + agentID,
		FilterSubject: bus.TaskQueue(swarm, agentID),
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
			t.Fatalf("unmarshal task: %v", err)
		}
		_ = msg.Ack()
		return &task, true
	}
	if err := batch.Error(); err != nil {
		t.Fatalf("fetch batch: %v", err)
	}
	return nil, false
}

func publishResult(t *testing.T, b *bus.Bus, swarm string, result *taskv1.TaskResult) {
	t.Helper()
	if err := b.PublishCore(bus.TaskResult(swarm, result.GetTaskId()), "test-worker", "trace-1", result); err != nil {
		t.Fatalf("publish result: %v", err)
	}
}

// waitWindow 轮询直到窗口达到 want 条（result 回路是异步的）。
func waitWindow(t *testing.T, m *session.Manager, sessionID string, want int) *session.Snapshot {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		snap, err := m.Snapshot(context.Background(), sessionID, 50)
		if err != nil {
			t.Fatalf("snapshot: %v", err)
		}
		if len(snap.Window) >= want {
			return snap
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatalf("窗口未达到 %d 条", want)
	return nil
}

// 触发：user 发言 → STRICT 轮到 agent → 入队 Task，字段齐备。
func TestMessageTriggersTask(t *testing.T) {
	b, m, d := newDispatcher(t)
	start(t, d)

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{user("u1"), agent("ideator")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	trigger := appendMsg(t, m, sess.GetSessionId(), user("u1"))

	task, ok := fetchTask(t, b, "sw1", "ideator")
	if !ok {
		t.Fatal("应有 task 入队")
	}
	if got := task.GetAgentId(); got != "ideator" {
		t.Errorf("agent_id = %q", got)
	}
	if got := task.GetSessionId(); got != sess.GetSessionId() {
		t.Errorf("session_id = %q", got)
	}
	if got := task.GetTriggerMessageId(); got != trigger.GetMessageId() {
		t.Errorf("trigger_message_id = %q, want %q", got, trigger.GetMessageId())
	}
	// 确定性 task id：重投/重启派生出同一个 id
	if want := "t_" + strings.TrimPrefix(trigger.GetMessageId(), "m_"); task.GetTaskId() != want {
		t.Errorf("task_id = %q, want %q", task.GetTaskId(), want)
	}
	if got := task.GetDeltaSubject(); got != bus.TaskDelta("sw1", task.GetTaskId()) {
		t.Errorf("delta_subject = %q", got)
	}
	if task.GetModel().GetModel() != "stub-model" {
		t.Errorf("model = %q", task.GetModel().GetModel())
	}
	// 窗口自包含：最后一条即触发消息
	w := task.GetContext().GetWindow()
	if len(w) != 1 || w[len(w)-1].GetMessageId() != trigger.GetMessageId() {
		t.Fatalf("window 应恰好含触发消息，got %d 条", len(w))
	}
}

// 不触发：下一个发言者是人 → 不入队。
func TestNoTriggerWhenUserIsNext(t *testing.T) {
	b, m, d := newDispatcher(t)
	start(t, d)

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{agent("ideator"), user("u1")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	appendMsg(t, m, sess.GetSessionId(), agent("ideator")) // agent 首发 → 轮到人

	if task, ok := fetchTask(t, b, "sw1", "ideator"); ok {
		t.Fatalf("不应入队，却取到 %v", task.GetTaskId())
	}
}

// 触发幂等：同一触发消息重复进入 trigger（重投/重启补触发），只入队一次。
func TestTriggerIdempotent(t *testing.T) {
	b, m, d := newDispatcher(t) // 不 Start：直接调内部 trigger，排除 consumer 干扰

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{user("u1"), agent("ideator")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	trigger := appendMsg(t, m, sess.GetSessionId(), user("u1"))

	ev := &sessionv1.SessionEvent{SessionId: sess.GetSessionId(), SwarmId: "sw1"}
	for i := 0; i < 3; i++ {
		if err := d.trigger(context.Background(), ev, trigger, ""); err != nil {
			t.Fatalf("trigger #%d: %v", i, err)
		}
	}
	if _, ok := fetchTask(t, b, "sw1", "ideator"); !ok {
		t.Fatal("应有一条 task")
	}
	if task, ok := fetchTask(t, b, "sw1", "ideator"); ok {
		t.Fatalf("重投不应重复入队，却取到 %v", task.GetTaskId())
	}
}

// result 回路：TaskResult(COMPLETED) → actor 落 MessageAppended；重投幂等。
func TestResultBecomesMessageAppended(t *testing.T) {
	b, m, d := newDispatcher(t)
	start(t, d)

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{user("u1"), agent("ideator")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	kickoff := appendMsg(t, m, sess.GetSessionId(), user("u1"))

	result := &taskv1.TaskResult{
		TaskId: "t_x",
		Status: taskv1.TaskStatus_TASK_STATUS_COMPLETED,
		Message: &sessionv1.Message{
			SessionId:        sess.GetSessionId(),
			From:             agent("ideator"),
			Content:          []*sessionv1.ContentBlock{text("stub reply")},
			ReplyToMessageId: kickoff.GetMessageId(),
		},
	}
	publishResult(t, b, "sw1", result)

	snap := waitWindow(t, m, sess.GetSessionId(), 2)
	got := snap.Window[1]
	if got.GetFrom().GetAgentId() != "ideator" {
		t.Errorf("from = %v", got.GetFrom())
	}
	if got.GetContent()[0].GetText() != "stub reply" {
		t.Errorf("text = %q", got.GetContent()[0].GetText())
	}
	if got.GetMessageId() != "m_task:t_x" { // client_msg_id 派生 message_id
		t.Errorf("message_id = %q", got.GetMessageId())
	}
	if got.GetReplyToMessageId() != kickoff.GetMessageId() {
		t.Errorf("reply_to = %q", got.GetReplyToMessageId())
	}
	if snap.Session.GetTurnIndex() != 2 {
		t.Errorf("turn_index = %d, want 2", snap.Session.GetTurnIndex())
	}

	// 重投：同一条 result 再发一次 → 仍只有 2 条（"task:t_x" 幂等去重）
	publishResult(t, b, "sw1", result)
	time.Sleep(300 * time.Millisecond)
	snap2, err := m.Snapshot(context.Background(), sess.GetSessionId(), 50)
	if err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	if len(snap2.Window) != 2 {
		t.Errorf("重投后窗口 = %d 条，want 2", len(snap2.Window))
	}
}

// FAILED / CANCELLED 不落消息（MVP 语义；turn 停滞由 Slice C 的 orchestrator 兜底）。
func TestFailedResultNotAppended(t *testing.T) {
	b, m, d := newDispatcher(t)
	start(t, d)

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{user("u1"), agent("ideator")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	appendMsg(t, m, sess.GetSessionId(), user("u1"))

	publishResult(t, b, "sw1", &taskv1.TaskResult{
		TaskId:  "t_fail",
		Status:  taskv1.TaskStatus_TASK_STATUS_FAILED,
		Error:   &commonv1.Error{Code: "WORKER_ERROR", Message: "boom"},
		Message: &sessionv1.Message{SessionId: sess.GetSessionId(), From: agent("ideator")},
	})
	time.Sleep(300 * time.Millisecond)
	snap, err := m.Snapshot(context.Background(), sess.GetSessionId(), 50)
	if err != nil {
		t.Fatalf("snapshot: %v", err)
	}
	if len(snap.Window) != 1 {
		t.Errorf("FAILED 不应落消息，窗口 = %d 条", len(snap.Window))
	}
}

// M1 全链路（Go 侧）：user 消息 → task 入队 → 假 worker 回 result → MessageAppended。
// Python worker 的离线验收在 tests/test_worker_offline.py；手动联调见 cmd/runtime。
func TestM1Loop(t *testing.T) {
	b, m, d := newDispatcher(t)
	start(t, d)

	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{user("u1"), agent("ideator")}, strictPolicy(), "")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	kickoff := appendMsg(t, m, sess.GetSessionId(), user("u1"))

	// 假 worker：取 task → 按 runner 约定回 result（单写者边界：不填 message_id / turn_index）
	task, ok := fetchTask(t, b, "sw1", "ideator")
	if !ok {
		t.Fatal("应有 task 入队")
	}
	publishResult(t, b, "sw1", &taskv1.TaskResult{
		TaskId: task.GetTaskId(),
		Status: taskv1.TaskStatus_TASK_STATUS_COMPLETED,
		Message: &sessionv1.Message{
			SessionId:        task.GetSessionId(),
			From:             agent(task.GetAgentId()),
			Content:          []*sessionv1.ContentBlock{text("fake worker reply")},
			ReplyToMessageId: task.GetTriggerMessageId(),
		},
		Usage: &taskv1.TaskUsage{Model: "stub-model"},
	})

	snap := waitWindow(t, m, sess.GetSessionId(), 2)
	if snap.Session.GetTurnIndex() != 2 {
		t.Errorf("turn_index = %d, want 2", snap.Session.GetTurnIndex())
	}
	if snap.Window[1].GetReplyToMessageId() != kickoff.GetMessageId() {
		t.Errorf("reply_to = %q", snap.Window[1].GetReplyToMessageId())
	}
	// 轮回到人：不应再有新 task
	if task, ok := fetchTask(t, b, "sw1", "ideator"); ok {
		t.Errorf("agent 回复后轮到 user，不应再入队，却取到 %v", task.GetTaskId())
	}
}
