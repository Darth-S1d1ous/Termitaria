// Package testutil 提供测试共用的内嵌 NATS server（JetStream）与 Bus 实例，
// 让单测不依赖 docker compose 起的基础设施。
package testutil

import (
	"context"
	"testing"
	"time"

	"github.com/nats-io/nats-server/v2/server"

	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
)

// NewBus 起一个内存级 NATS server（JetStream 开启，存储在 t.TempDir），
// 连上 Bus 并 provision 三条 stream。随测试结束自动清理。
func NewBus(t *testing.T) *bus.Bus {
	t.Helper()

	s, err := server.NewServer(&server.Options{
		Port:      -1, // 随机端口，可并行
		JetStream: true,
		StoreDir:  t.TempDir(),
	})
	if err != nil {
		t.Fatalf("nats server init: %v", err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		t.Fatal("nats server not ready")
	}
	t.Cleanup(s.Shutdown)

	b, err := bus.Connect(s.ClientURL())
	if err != nil {
		t.Fatalf("bus connect: %v", err)
	}
	t.Cleanup(b.Close)

	if err := b.EnsureStreams(context.Background()); err != nil {
		t.Fatalf("ensure streams: %v", err)
	}
	return b
}
