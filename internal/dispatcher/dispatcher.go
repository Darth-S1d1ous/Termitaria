package dispatcher

import (
	"context"
	"fmt"
	"log"
	"strings"
	"sync"
	"time"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/protobuf/proto"
)

const producer = "dispatcher"

type AgentConfig struct {
	Model  *commonv1.ModelRef
	Budget *commonv1.Budget
}

type AgentRegistry interface {
	Lookup(agentID string) (AgentConfig, bool)
}

type StaticRegistry map[string]AgentConfig

func (r StaticRegistry) Lookup(agentID string) (AgentConfig, bool) {
	cfg, ok := r[agentID]
	return cfg, ok
}

// Dispatcher 订阅 session 事件流与 task result，做触发与回写。
type Dispatcher struct {
	bus         *bus.Bus
	sessions    *session.Manager
	registry    AgentRegistry
	windowLimit int

	mu        sync.Mutex
	triggered map[string]string

	cc     jetstream.ConsumeContext
	resSub *nats.Subscription
}

func New(b *bus.Bus, sessions *session.Manager, registry AgentRegistry, windowLimit int) *Dispatcher {
	return &Dispatcher{
		bus:         b,
		sessions:    sessions,
		registry:    registry,
		windowLimit: windowLimit,
		triggered:   make(map[string]string),
	}
}

// Start 建立两路订阅后返回（非阻塞）：
//   - SESSIONS 上的 DeliverNew durable consumer：只触发实时事件；停机期间的事件由
//     durable 游标补上，确定性 task id 保证补触发不产生副作用。
//   - tasks.*.result.* 上的 core NATS queue group：runtime 多副本间负载均衡。
func (d *Dispatcher) Start(ctx context.Context) error {
	cons, err := d.bus.JetStream().CreateOrUpdateConsumer(ctx, "SESSIONS", jetstream.ConsumerConfig{
		Durable:       "dispatcher-trigger",
		FilterSubject: bus.SessionEventsAll,
		DeliverPolicy: jetstream.DeliverNewPolicy,
		AckPolicy:     jetstream.AckExplicitPolicy,
	})
	if err != nil {
		return fmt.Errorf("trigger consumer: %w", err)
	}
	cc, err := cons.Consume(d.onSessionEvent)
	if err != nil {
		return fmt.Errorf("consume sessions: %w", err)
	}
	sub, err := d.bus.Conn().QueueSubscribe(bus.TaskResultAll, "agent-runtime", d.onTaskResult)
	if err != nil {
		cc.Stop()
		return fmt.Errorf("subscribe results: %w", err)
	}
	d.cc, d.resSub = cc, sub
	log.Printf("dispatcher up: trigger=%s results=%s", bus.SessionEventsAll, bus.TaskResultAll)
	return nil
}
func (d *Dispatcher) Stop() {
	if d.cc != nil {
		d.cc.Stop()
	}
	if d.resSub != nil {
		_ = d.resSub.Drain()
	}
}

// onSessionEvent：只有 MessageAppended 是触发源（contracts §4：worker = 消息/turn 驱动）。
func (d *Dispatcher) onSessionEvent(msg jetstream.Msg) {
	var ev sessionv1.SessionEvent
	env, err := bus.Unmarshal(msg.Data(), &ev)
	if err != nil {
		log.Printf("unparseable session event, ack to drop: %v", err)
		_ = msg.Ack()
		return
	}
	appended := ev.GetMessageAppended()
	if appended == nil {
		_ = msg.Ack() // TurnAdvanced / Suspended / Closed 不产生 task
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if err := d.trigger(ctx, &ev, appended.GetMessage(), env.GetTraceId()); err != nil {
		log.Printf("trigger session=%s: %v（nak 重投；确定性 task id 保证重投安全）", ev.GetSessionId(), err)
		_ = msg.NakWithDelay(5 * time.Second)
		return
	}
	_ = msg.Ack()
}

// trigger 决定是否为这条消息入队一个 worker task。
func (d *Dispatcher) trigger(ctx context.Context, ev *sessionv1.SessionEvent, msg *sessionv1.Message, traceID string) error {
	snap, err := d.sessions.Snapshot(ctx, ev.GetSessionId(), d.windowLimit)
	if err != nil {
		return fmt.Errorf("snapshot: %w", err)
	}
	sess := snap.Session
	if sess.GetState() != sessionv1.SessionState_SESSION_STATE_OPEN {
		return nil // 挂起（max_turns 耗尽）/ 关闭：不再触发
	}
	next := nextSpeaker(sess, msg)
	if next == nil {
		return nil
	}
	agentID := next.GetAgentId()
	if agentID == "" {
		return nil // 下一个发言者是人：人走 AppendMessage，不产生 task
	}
	cfg, ok := d.registry.Lookup(agentID)
	if !ok {
		log.Printf("agent %q 不在 registry，跳过（MVP 静态配置；Scheduler 落地后消失）", agentID)
		return nil
	}
	// 触发幂等：同一触发消息只入队一次。task id 由触发消息 id 派生——进程重启 /
	// 事件重投后派生出同一个 id，worker 的 _seen 与 actor 的 client_msg_id 去重
	// （"task:<task_id>"）共同兜底。
	d.mu.Lock()
	defer d.mu.Unlock()
	triggerID := msg.GetMessageId()
	if _, dup := d.triggered[triggerID]; dup {
		return nil
	}
	taskID := "t_" + strings.TrimPrefix(triggerID, "m_")
	task := &taskv1.Task{
		TaskId:           taskID,
		SwarmId:          ev.GetSwarmId(),
		AgentId:          agentID,
		SessionId:        sess.GetSessionId(),
		TriggerMessageId: triggerID,
		Context: &taskv1.TaskContext{
			Window: snap.Window,
			// recalled / graph_node_ids 留空：MemoryService 未接，契约已预留（contracts §4 自包含）。
		},
		Budget:       cfg.Budget,
		Model:        cfg.Model,
		DeltaSubject: bus.TaskDelta(ev.GetSwarmId(), taskID),
		EnqueuedAtMs: time.Now().UnixMilli(),
	}
	if _, err := d.bus.Publish(ctx, bus.TaskQueue(ev.GetSwarmId(), agentID), producer, traceID, task); err != nil {
		return fmt.Errorf("enqueue task: %w", err)
	}
	d.triggered[triggerID] = taskID
	log.Printf("task %s → %s（trigger=%s trace=%s）", taskID, agentID, triggerID, traceID)
	return nil
}

// nextSpeaker 决定谁接话；返回 nil = 无需触发。
// STRICT：actor 落消息时已推进 turn_index，当前值即下一个发言者的下标。
// FREE：两人 session 里取「非最后发言者」。
func nextSpeaker(sess *sessionv1.Session, last *sessionv1.Message) *sessionv1.Participant {
	ps := sess.GetParticipants()
	if len(ps) != 2 { // MVP 恰好 2 人（ErrNeedTwoParticipants）
		return nil
	}
	if sess.GetPolicy().GetTurnTaking() == sessionv1.TurnTaking_TURN_TAKING_STRICT {
		return ps[sess.GetTurnIndex()%uint32(len(ps))]
	}
	if proto.Equal(ps[0], last.GetFrom()) {
		return ps[1]
	}
	return ps[0]
}

// onTaskResult 把 worker 的最终结果路由回 session actor——
// agent 消息只经此回路落流（单写者，contracts §1.2）。
func (d *Dispatcher) onTaskResult(msg *nats.Msg) {
	var result taskv1.TaskResult
	env, err := bus.Unmarshal(msg.Data, &result)
	if err != nil {
		log.Printf("unparseable task result, drop: %v", err)
		return // core NATS 无重投，坏消息直接丢
	}
	if result.GetStatus() != taskv1.TaskStatus_TASK_STATUS_COMPLETED {
		// FAILED / CANCELLED：MVP 不落消息。
		// 已知缺口：STRICT 会话的 turn 会停滞——Slice C 由 orchestrator 的
		// SPOKE_STALLED 触发介入兜底（contracts §4 触发语义）。
		log.Printf("task %s status=%s code=%s：不落消息",
			result.GetTaskId(), result.GetStatus(), result.GetError().GetCode())
		return
	}
	m := result.GetMessage()
	if m == nil {
		log.Printf("task %s COMPLETED 但 message 为空，丢弃", result.GetTaskId())
		return
	}
	if u := result.GetUsage(); u != nil {
		log.Printf("task %s usage: model=%s tokens=%d+%d cost=$%.4f",
			result.GetTaskId(), u.GetModel(), u.GetPromptTokens(), u.GetCompletionTokens(), u.GetCostUsd())
	}
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	// client_msg_id 复用 actor 的幂等去重：result 重投 / worker 重跑都只落一条。
	_, err = d.sessions.AppendMessage(ctx, m.GetSessionId(), m.GetFrom(), m.GetContent(),
		m.GetReplyToMessageId(), "task:"+result.GetTaskId(), env.GetTraceId())
	if err != nil {
		// ErrSessionClosed/Suspended = 迟到结果（turn 已终结）；ErrNotYourTurn 不该发生
		// （触发时已校验轮转）——发生即 bug，都先记录。
		log.Printf("append result task=%s session=%s: %v", result.GetTaskId(), m.GetSessionId(), err)
	}
}
