package dispatcher

import (
	"context"
	"fmt"
	"log"
	"os"
	"strconv"
	"strings"
	"sync"
	"time"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
	memoryv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/memory/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	taskv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/task/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/live"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
	"google.golang.org/protobuf/proto"
)

const producer = "dispatcher"

// defaultRecallWait 在还没有 data/recall-wait-ms 时使用。
// 20:45 那次真实检索大约 700ms 才返回，所以下限是 1200ms。
// 记忆服务启动后会重写那个文件，但不会低于这个下限。
const defaultRecallWait = 1200 * time.Millisecond

type AgentConfig struct {
	Model  *commonv1.ModelRef
	Budget *commonv1.Budget
	// RolePrompt 是 SwarmSpec 的角色设定。MemoryService 未接，
	// 由 dispatcher 作为 ROLE 记忆注入 task（worker 的 recalled 优先于 fallback）。
	RolePrompt string
}

type AgentRegistry interface {
	Lookup(agentID string) (AgentConfig, bool)
}

type StaticRegistry map[string]AgentConfig

func (r StaticRegistry) Lookup(agentID string) (AgentConfig, bool) {
	cfg, ok := r[agentID]
	return cfg, ok
}

// Registry 是可在运行中登记 agent 的名册。新建项目时把 SwarmSpec 里的 agent 登记进来。
type Registry struct {
	mu    sync.RWMutex
	items map[string]AgentConfig
}

func NewRegistry() *Registry {
	return &Registry{items: map[string]AgentConfig{}}
}

func (r *Registry) Lookup(agentID string) (AgentConfig, bool) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	cfg, ok := r.items[agentID]
	return cfg, ok
}

func (r *Registry) Register(agentID string, cfg AgentConfig) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.items[agentID] = cfg
}

// Dispatcher 订阅 session 事件流与 task result，做触发与回写。
type Dispatcher struct {
	bus           *bus.Bus
	sessions      *session.Manager
	registry      AgentRegistry
	windowLimit   int
	recallTimeout time.Duration

	mu        sync.Mutex
	triggered map[string]string

	cc          jetstream.ConsumeContext
	resSub      *nats.Subscription
	hub         *live.Hub
	skipSpeaker func(sessionID, agentID string) bool
}

func (d *Dispatcher) SetHub(h *live.Hub) { d.hub = h }

// SetSkipSpeaker 在入队前问一句要不要跳过这个发言者。nil 表示谁该说都入队。
func (d *Dispatcher) SetSkipSpeaker(fn func(sessionID, agentID string) bool) {
	d.skipSpeaker = fn
}

func New(b *bus.Bus, sessions *session.Manager, registry AgentRegistry, windowLimit int) *Dispatcher {
	return &Dispatcher{
		bus:           b,
		sessions:      sessions,
		registry:      registry,
		windowLimit:   windowLimit,
		recallTimeout: recallWait(),
		triggered:     make(map[string]string),
	}
}

// recallWait 是 memory.recall 的等待。
// RECALL_TIMEOUT_MS 优先。否则读记忆服务写下的 data/recall-wait-ms。
// 那个文件不低于 1200ms。热查询更慢时，文件里是较慢的一次再留余量。
func recallWait() time.Duration {
	if ms := envOrUint64("RECALL_TIMEOUT_MS", 0); ms > 0 {
		return time.Duration(ms) * time.Millisecond
	}
	raw, err := os.ReadFile("data/recall-wait-ms")
	if err == nil {
		n, parseErr := strconv.ParseUint(strings.TrimSpace(string(raw)), 10, 64)
		if parseErr == nil && n > 0 {
			return time.Duration(n) * time.Millisecond
		}
	}
	return defaultRecallWait
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
	closeGraph()
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
	// 轮次已经走到上限就不要再叫醒下一个人。挂起事件可能还没被这次快照看见。
	if mt := sess.GetPolicy().GetMaxTurns(); mt > 0 && sess.GetTurnIndex() >= mt {
		return nil
	}
	next := nextSpeaker(sess, msg)
	if next == nil {
		return nil
	}
	agentID := next.GetAgentId()
	if agentID == "" {
		return nil // 下一个发言者是人：人走 AppendMessage，不产生 task
	}
	if d.skipSpeaker != nil && d.skipSpeaker(ev.GetSessionId(), agentID) {
		return nil
	}
	cfg, ok := d.registry.Lookup(agentID)
	if !ok {
		log.Printf("agent %q 不在 registry，跳过（MVP 静态配置；Scheduler 落地后消失）", agentID)
		return nil
	}
	model := cfg.Model
	if selected := msg.GetModel(); selected.GetModel() != "" {
		model = selected
	}
	recalled, err := d.recall(ctx, ev.GetSwarmId(), agentID, messageText(msg), traceID)
	if err != nil {
		log.Printf("memory.recall agent=%s: %v（保持角色条目）", agentID, err)
		recalled = nil
	}
	nodeIDs, knowledge := d.projectKnowledge(ctx, ev.GetSwarmId(), snap.Window)
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
	context := &taskv1.TaskContext{
		Window:       snap.Window,
		Recalled:     mergeRecalled(agentID, cfg.RolePrompt, recalled),
		GraphNodeIds: nodeIDs,
		Knowledge:    knowledge,
	}
	task := &taskv1.Task{
		TaskId:           taskID,
		SwarmId:          ev.GetSwarmId(),
		AgentId:          agentID,
		SessionId:        sess.GetSessionId(),
		TriggerMessageId: triggerID,
		Context:          context,
		Budget:           cfg.Budget,
		Model:            model,
		DeltaSubject:     bus.TaskDelta(ev.GetSwarmId(), taskID),
		EnqueuedAtMs:     time.Now().UnixMilli(),
	}
	if d.hub != nil {
		d.hub.Track(live.TaskMeta{
			TaskID: taskID, SessionID: sess.GetSessionId(), AgentID: agentID, SwarmID: ev.GetSwarmId(),
		})
	}
	if _, err := d.bus.Publish(ctx, bus.TaskQueue(ev.GetSwarmId(), agentID), producer, traceID, task); err != nil {
		if d.hub != nil {
			d.hub.Finish(taskID)
		}
		return fmt.Errorf("enqueue task: %w", err)
	}
	d.triggered[triggerID] = taskID
	log.Printf("task %s → %s（trigger=%s trace=%s）", taskID, agentID, triggerID, traceID)
	return nil
}

// recall 在 dispatch 前同步问 memory.recall。超时见 recallWait。失败则调用方保留静态角色。
func (d *Dispatcher) recall(ctx context.Context, projectID, agentID, query, traceID string) ([]*memoryv1.MemoryItem, error) {
	req := &memoryv1.RecallRequest{
		AgentId:   agentID,
		ProjectId: projectID,
		QueryText: query,
		Depth:     memoryv1.RecallDepth_RECALL_DEPTH_AUTO,
		TopK:      5,
	}
	env, err := bus.NewEnvelope(producer, traceID, req)
	if err != nil {
		return nil, err
	}
	data, err := proto.Marshal(env)
	if err != nil {
		return nil, err
	}
	call, cancel := context.WithTimeout(ctx, d.recallTimeout)
	defer cancel()
	msg, err := d.bus.Conn().RequestWithContext(call, bus.MemoryRecall, data)
	if err != nil {
		return nil, err
	}
	var resp memoryv1.RecallResponse
	if _, err := bus.Unmarshal(msg.Data, &resp); err != nil {
		return nil, err
	}
	return resp.GetItems(), nil
}

// mergeRecalled 把 SwarmSpec 角色接进召回结果。
// 后端没给 ROLE 时，沿用原来的静态角色条目。已有 ROLE 且角色文案非空时，用角色文案盖住后端那条。
func mergeRecalled(agentID, rolePrompt string, items []*memoryv1.MemoryItem) []*memoryv1.MemoryItem {
	hasRole := false
	for _, it := range items {
		if it.GetKind() != memoryv1.MemoryKind_MEMORY_KIND_ROLE {
			continue
		}
		hasRole = true
		if rolePrompt != "" {
			it.Text = rolePrompt
		}
	}
	if hasRole || rolePrompt == "" {
		return items
	}
	role := &memoryv1.MemoryItem{
		ItemId:  "role:" + agentID,
		AgentId: agentID,
		Kind:    memoryv1.MemoryKind_MEMORY_KIND_ROLE,
		Text:    rolePrompt,
	}
	return append([]*memoryv1.MemoryItem{role}, items...)
}

func messageText(msg *sessionv1.Message) string {
	parts := make([]string, 0, len(msg.GetContent()))
	for _, block := range msg.GetContent() {
		if t := block.GetText(); t != "" {
			parts = append(parts, t)
		}
	}
	return strings.Join(parts, "\n")
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
	if d.hub != nil {
		d.hub.Finish(result.GetTaskId())
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
