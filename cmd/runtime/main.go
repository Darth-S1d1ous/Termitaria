package main

import (
	"context"
	"flag"
	"log"
	"os"
	"os/signal"
	"strings"
	"syscall"
	"time"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/dispatcher"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
)

func main() {
	natsURL := flag.String("nats", envOr("NATS_URL", "nats://localhost:4222"), "NATS URL")
	agents := flag.String("agents", "", "逗号分隔的 agent id（MVP 统一 stub 模型配置）")
	demo := flag.Bool("demo", false, "脚手架：开一条 user↔agents[0] 的 session 并发开场消息")
	flag.Parse()

	ids := splitCSV(*agents)
	if len(ids) == 0 {
		log.Fatal("-agents is empty: no agents to dispatch to")
	}

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

	// MVP：所有 agent 共用 stub 模型配置——Scheduler 落地后由 SwarmSpec 编译产物替代。
	registry := dispatcher.StaticRegistry{}
	for _, id := range ids {
		registry[id] = dispatcher.AgentConfig{
			Model: &commonv1.ModelRef{
				Provider: "nebius-token-factory",
				Tier:     commonv1.ModelTier_MODEL_TIER_SUPER,
				Model:    "stub-model",
			},
			Budget: &commonv1.Budget{MaxTokens: 512},
		}
	}

	manager := session.NewManager(b)
	d := dispatcher.New(b, manager, registry, 50)
	if err := d.Start(ctx); err != nil {
		log.Fatalf("dispatcher start: %v", err)
	}
	defer d.Stop()
	if *demo {
		openDemoSession(manager, ids[0])
	}
	log.Printf("runtime up: agents=%v nats=%s", ids, *natsURL)
	<-ctx.Done()
	log.Println("shutting down")
}

// openDemoSession 是 M1 手动验收的脚手架；SessionService（Slice D）落地后删除。
func openDemoSession(m *session.Manager, agentID string) {
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	sess, err := m.OpenSession(ctx, "sw1",
		[]*sessionv1.Participant{
			{Kind: &sessionv1.Participant_UserId{UserId: "u_demo"}},
			{Kind: &sessionv1.Participant_AgentId{AgentId: agentID}},
		},
		&sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT},
		"")
	if err != nil {
		log.Fatalf("demo open session: %v", err)
	}
	// client_msg_id 留空：每次 demo 生成全新 ULID，避免跨运行派生出相同 task_id
	// （僵尸 worker 的 _seen 会把「重复」任务直接吞掉——真实踩过的坑）。
	if _, err := m.AppendMessage(ctx, sess.GetSessionId(),
		&sessionv1.Participant{Kind: &sessionv1.Participant_UserId{UserId: "u_demo"}},
		[]*sessionv1.ContentBlock{{Block: &sessionv1.ContentBlock_Text{Text: "介绍一下你自己"}}},
		"", "", ""); err != nil {
		log.Fatalf("demo append: %v", err)
	}
	log.Printf("demo session %s：user 已开场，等 worker 回复", sess.GetSessionId())
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
