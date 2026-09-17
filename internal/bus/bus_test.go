package bus_test

import (
	"context"
	"strings"
	"testing"
	"time"

	"google.golang.org/protobuf/proto"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/testutil"
)

// subject 分类法的布局约束（contracts.md §2）。
func TestSubjectLayout(t *testing.T) {
	if got := bus.SessionSubject("sw1", "s1"); got != "sessions.sw1.s1.events" {
		t.Errorf("SessionSubject = %q", got)
	}
	if got := bus.TaskQueue("sw1", "ag1"); got != "tasks.sw1.ag1" {
		t.Errorf("TaskQueue = %q", got)
	}
	// delta/result 必须是 4 段 token，才不会被 TASKS stream 的 tasks.*.* 捕获——
	// 「delta 不持久化」靠这个段数差实现。
	if n := len(strings.Split(bus.TaskDelta("sw1", "t1"), ".")); n != 4 {
		t.Errorf("TaskDelta token count = %d, want 4", n)
	}
	if n := len(strings.Split(bus.TaskResult("sw1", "t1"), ".")); n != 4 {
		t.Errorf("TaskResult token count = %d, want 4", n)
	}
}

// EnsureStreams 幂等：跑两次不报错，三条 stream 都在。
func TestEnsureStreamsIdempotent(t *testing.T) {
	b := testutil.NewBus(t) // 内部已跑过一次
	if err := b.EnsureStreams(context.Background()); err != nil {
		t.Fatalf("second EnsureStreams: %v", err)
	}
	for _, name := range []string{"SESSIONS", "TASKS", "MEMORY"} {
		if _, err := b.JetStream().Stream(context.Background(), name); err != nil {
			t.Errorf("stream %s missing: %v", name, err)
		}
	}
}

// 行为级验证：core NATS 上的 delta 不落 TASKS stream，工作队列消息才落。
func TestTaskDeltaNotPersisted(t *testing.T) {
	b := testutil.NewBus(t)
	ctx := context.Background()

	if err := b.PublishCore(bus.TaskDelta("sw1", "t1"), "test", "", &taskv1.TaskDelta{TaskId: "t1", Seq: 1}); err != nil {
		t.Fatalf("publish delta: %v", err)
	}
	if _, err := b.Publish(ctx, bus.TaskQueue("sw1", "ag1"), "test", "", &taskv1.Task{TaskId: "t1"}); err != nil {
		t.Fatalf("publish task: %v", err)
	}

	st, err := b.JetStream().Stream(ctx, "TASKS")
	if err != nil {
		t.Fatalf("get TASKS stream: %v", err)
	}
	info, err := st.Info(ctx)
	if err != nil {
		t.Fatalf("stream info: %v", err)
	}
	if info.State.Msgs != 1 {
		t.Errorf("TASKS stream msgs = %d, want 1（delta 不应被持久化）", info.State.Msgs)
	}
}

// Envelope 往返：schema 自动登记、trace 透传、payload 无损。
func TestEnvelopeRoundTrip(t *testing.T) {
	payload := &sessionv1.SessionEvent{
		SessionId: "s1",
		SwarmId:   "sw1",
		Event: &sessionv1.SessionEvent_Closed{
			Closed: &sessionv1.SessionClosed{Reason: "done"},
		},
	}
	env, err := bus.NewEnvelope("test-producer", "trace-1", payload)
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}
	if env.EventId == "" {
		t.Error("event_id empty")
	}
	if env.TraceId != "trace-1" {
		t.Errorf("trace_id = %q, want trace-1", env.TraceId)
	}
	if env.Schema != "termitaria.session.v1.SessionEvent" {
		t.Errorf("schema = %q", env.Schema)
	}
	if env.OccurredAtMs <= 0 || env.OccurredAtMs > time.Now().UnixMilli() {
		t.Errorf("occurred_at_ms = %d 不合理", env.OccurredAtMs)
	}

	data, err := proto.Marshal(env)
	if err != nil {
		t.Fatalf("marshal envelope: %v", err)
	}
	var got sessionv1.SessionEvent
	env2, err := bus.Unmarshal(data, &got)
	if err != nil {
		t.Fatalf("Unmarshal: %v", err)
	}
	if env2.EventId != env.EventId {
		t.Errorf("event_id 往返不一致")
	}
	if !proto.Equal(payload, &got) {
		t.Errorf("payload 往返不一致: got %v", &got)
	}
}

// schema 与目标类型不一致必须报错，而不是静默产出零值消息。
func TestUnmarshalSchemaMismatch(t *testing.T) {
	env, err := bus.NewEnvelope("test", "", &sessionv1.SessionEvent{SessionId: "s1"})
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}
	data, _ := proto.Marshal(env)
	var wrong sessionv1.Message
	if _, err := bus.Unmarshal(data, &wrong); err == nil {
		t.Fatal("schema mismatch 应报错")
	}
}

// trace_id 留空时自动生成（链路起点）；event_id 全局唯一。
func TestNewEnvelopeGeneratesIDs(t *testing.T) {
	e1, err := bus.NewEnvelope("test", "", &sessionv1.SessionEvent{})
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}
	e2, err := bus.NewEnvelope("test", "", &sessionv1.SessionEvent{})
	if err != nil {
		t.Fatalf("NewEnvelope: %v", err)
	}
	if e1.TraceId == "" {
		t.Error("trace_id 应自动生成")
	}
	if e1.EventId == e2.EventId {
		t.Error("event_id 应唯一")
	}
}
