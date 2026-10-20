package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/nats-io/nats.go/jetstream"

	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/dispatcher"
	"github.com/Darth-S1d1ous/Termitaria/internal/gateway"
	"github.com/Darth-S1d1ous/Termitaria/internal/live"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/Darth-S1d1ous/Termitaria/internal/swarm"
)

// colony 在进程内拉起 agent：登记角色与预算，并把 presence 标成在线。
type colony struct {
	reg *dispatcher.Registry
	hub *live.Hub
}

func (c colony) Start(id, rolePrompt string, maxTokens uint32) {
	cfg, ok := c.reg.Lookup(id)
	if !ok {
		cfg = dispatcher.FromEnv()
	}
	if rolePrompt != "" {
		cfg.RolePrompt = rolePrompt
	}
	if maxTokens > 0 && cfg.Budget != nil {
		budget := *cfg.Budget
		budget.MaxTokens = maxTokens
		cfg.Budget = &budget
	}
	c.reg.Register(id, cfg)
	c.hub.SetPresence(id, "online")
}

func main() {
	natsURL := flag.String("nats", envOr("NATS_URL", "nats://localhost:4222"), "NATS URL")
	agents := flag.String("agents", "", "可选。额外登记的 agent id；角色与名册来自 SwarmSpec，以及运行中创建的项目")
	swarmDir := flag.String("swarmdir", envOr("SWARM_DIR", "examples/swarms"),
		"SwarmSpec 目录：registry 注入 rolePrompt/budget，gateway 用它做项目模板")
	httpAddr := flag.String("http", envOr("GATEWAY_ADDR", "127.0.0.1:8787"),
		"gateway 监听地址（空 = 不启动）。必须与 dispatcher 同进程——session 单写者")
	statePath := flag.String("state", envOr("GATEWAY_STATE", "data/gateway-state.json"), "gateway 项目/session 映射状态文件")
	replyTimeoutMs := flag.Uint64("reply-timeout-ms", envOrUint64("GATEWAY_REPLY_TIMEOUT_MS", 300000), "gateway 等 agent 回复的超时")
	demo := flag.Bool("demo", false, "脚手架：开一条 agents[0]↔agents[1] 的 session，由前者种 kickoff")
	maxTurns := flag.Uint("max-turns", 6, "demo session 轮次上限（SessionPolicy.max_turns），耗尽后挂起；0 = 不限")
	idleTimeoutMs := flag.Uint64("idle-timeout-ms",
		envOrUint64("SESSION_IDLE_TIMEOUT_MS", 10*60*1000), // 暂定 10 分钟
		"session 空闲自动关闭超时（SessionPolicy.idle_timeout_ms），0 = 不自动关闭")
	flag.Parse()

	ids := splitCSV(*agents)

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	b, err := bus.Connect(*natsURL)
	if err != nil {
		log.Fatalf("bus connect: %v", err)
	}
	defer b.Close()

	if err := b.EnsureStreams(ctx); err != nil {
		log.Fatalf("ensure streams: %v", err)
	}

	// SwarmSpec 是 registry 与 gateway 的共同名册：按 agent id 注入 rolePrompt/budget。
	// 目录读失败不致命——demo 场景没有 spec 也能跑（rolePrompt 为空，worker 用 fallback）。
	var specs []*swarm.Spec
	if loaded, err := swarm.LoadDir(*swarmDir); err != nil {
		log.Printf("swarmdir %s: %v（rolePrompt 注入与 gateway 模板停用）", *swarmDir, err)
	} else {
		specs = loaded
	}
	registry := dispatcher.NewRegistry()
	hub := live.New()
	started := colony{reg: registry, hub: hub}
	var roster []string
	for _, spec := range specs {
		for _, a := range spec.Agents {
			started.Start(a.ID, a.RolePrompt, a.Budget.MaxTokens)
			roster = append(roster, a.ID)
		}
	}
	for _, id := range ids {
		if _, ok := registry.Lookup(id); !ok {
			started.Start(id, "", 0)
			roster = append(roster, id)
		}
	}
	pair := roster
	if len(ids) > 0 {
		pair = ids
	}
	if len(roster) == 0 {
		log.Fatal("没有可调度的 agent：SwarmSpec 为空，也没有 -agents")
	}
	if *demo && len(pair) < 2 {
		log.Fatal("-demo 需要至少两个 agent（来自 SwarmSpec 或 -agents）")
	}

	// Gateway 落地前的观测面：把每条 MessageAppended 打出来。订在开 session 之前，kickoff 也能看见。
	stopWatch, err := watchMessages(ctx, b)
	if err != nil {
		log.Fatalf("watch sessions: %v", err)
	}
	defer stopWatch()

	manager := session.NewManager(b)
	d := dispatcher.New(b, manager, registry, 50)
	d.SetHub(hub)
	if err := d.Start(ctx); err != nil {
		log.Fatalf("dispatcher start: %v", err)
	}
	defer d.Stop()
	if *httpAddr != "" {
		if len(specs) == 0 {
			log.Fatalf("gateway 需要 SwarmSpec 模板：-swarmdir %s 无可读 spec", *swarmDir)
		}
		gw, err := gateway.New(manager, specs, *statePath, time.Duration(*replyTimeoutMs)*time.Millisecond, hub, started, *swarmDir)
		if err != nil {
			log.Fatalf("gateway state: %v", err)
		}
		if err := gw.Start(ctx, b); err != nil {
			log.Fatalf("gateway subscribe: %v", err)
		}
		go func() {
			if err := gw.ListenAndServe(*httpAddr); err != nil {
				log.Fatalf("gateway serve: %v", err)
			}
		}()
	}
	if *demo {
		openDemoSession(manager, pair[0], pair[1], *idleTimeoutMs, uint32(*maxTurns))
	}
	cfg, _ := registry.Lookup(roster[0])
	log.Printf("runtime up: agents=%v nats=%s model=%s", roster, *natsURL, cfg.Model.GetModel())
	<-ctx.Done()
	log.Println("shutting down")
}

// openDemoSession 是双 agent 通信的手动验收脚手架；SessionService（Slice D）落地后删除。
// kickoff 以 participants[0] 的身份直接落流，不经过 worker——STRICT 下第一条必须由它来写，
// 对端才是第一次真正推理。max_turns 把乒乓停在挂起，空闲超时在对聊期间不会触发。
func openDemoSession(m *session.Manager, openerID, peerID string, idleTimeoutMs uint64, maxTurns uint32) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	opener := &sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: openerID}}
	peer := &sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: peerID}}
	sess, err := m.OpenSession(ctx, "sw1",
		[]*sessionv1.Participant{opener, peer},
		&sessionv1.SessionPolicy{
			TurnTaking:    sessionv1.TurnTaking_TURN_TAKING_STRICT,
			MaxTurns:      maxTurns,
			IdleTimeoutMs: idleTimeoutMs,
		},
		"")
	if err != nil {
		log.Fatalf("demo open session: %v", err)
	}
	// client_msg_id 留空：每次 demo 生成全新 ULID，避免跨运行派生出相同 task_id
	// （僵尸 worker 的 _seen 会把「重复」任务直接吞掉——真实踩过的坑）。
	if _, err := m.AppendMessage(ctx, sess.GetSessionId(), opener,
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: "我有一个待评审的假设：把开场写成一条普通消息，对端就能接上对话。请回应。"}}},
		"", "", ""); err != nil {
		log.Fatalf("demo kickoff: %v", err)
	}
	log.Printf("demo session %s：%s 已种 kickoff（计入 max_turns=%d），下一发言者 %s",
		sess.GetSessionId(), openerID, maxTurns, peerID)
}

// watchMessages 订阅 sessions.>，只打印 MessageAppended。其余事件 ack 后丢掉。
func watchMessages(ctx context.Context, b *bus.Bus) (func(), error) {
	cons, err := b.JetStream().CreateOrUpdateConsumer(ctx, "SESSIONS", jetstream.ConsumerConfig{
		Durable:       "runtime-session-log",
		FilterSubject: bus.SessionEventsAll,
		DeliverPolicy: jetstream.DeliverNewPolicy,
		AckPolicy:     jetstream.AckExplicitPolicy,
	})
	if err != nil {
		return nil, err
	}
	cc, err := cons.Consume(func(msg jetstream.Msg) {
		var ev sessionv1.SessionEvent
		if _, err := bus.Unmarshal(msg.Data(), &ev); err != nil {
			log.Printf("unparseable session event: %v", err)
			_ = msg.Ack()
			return
		}
		appended := ev.GetMessageAppended()
		if appended == nil {
			_ = msg.Ack()
			return
		}
		m := appended.GetMessage()
		log.Printf("msg session=%s from=%s: %s", ev.GetSessionId(), participantLabel(m.GetFrom()), oneLine(messageText(m)))
		_ = msg.Ack()
	})
	if err != nil {
		return nil, err
	}
	log.Printf("watching %s", bus.SessionEventsAll)
	return cc.Stop, nil
}

func participantLabel(p *sessionv1.Participant) string {
	if id := p.GetAgentId(); id != "" {
		return id
	}
	if id := p.GetUserId(); id != "" {
		return id
	}
	return "?"
}

func messageText(m *sessionv1.Message) string {
	for _, block := range m.GetContent() {
		if text := block.GetText(); text != "" {
			return text
		}
	}
	return ""
}

func oneLine(s string) string {
	return strings.Join(strings.Fields(s), " ")
}

func splitCSV(s string) []string {
	var out []string
	for _, part := range strings.Split(s, ",") {
		if p := strings.TrimSpace(part); p != "" {
			out = append(out, p)
		}
	}
	return out
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

func envOrUint64(key string, fallback uint64) uint64 {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.ParseUint(v, 10, 64); err == nil {
			return n
		}
	}
	return fallback
}
