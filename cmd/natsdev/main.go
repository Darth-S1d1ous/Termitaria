// 基础设施替身
// 与 internal/testutil 同款内嵌方式；docker compose 不可用时的替代。
//
//	go run ./cmd/natsdev
package main

import (
	"log"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/nats-io/nats-server/v2/server"
)

func main() {
	dir, err := os.MkdirTemp("", "termitaria-nats-")
	if err != nil {
		log.Fatalf("temp dir: %v", err)
	}
	s, err := server.NewServer(&server.Options{
		Port:      4222,
		JetStream: true,
		StoreDir:  dir,
		HTTPPort:  8222, // 监控：http://localhost:8222/varz
	})
	if err != nil {
		log.Fatalf("nats server init: %v", err)
	}
	go s.Start()
	if !s.ReadyForConnections(10 * time.Second) {
		log.Fatal("nats server not ready")
	}
	log.Printf("nats dev server up: nats://localhost:4222 (store=%s)", dir)

	sig := make(chan os.Signal, 1)
	signal.Notify(sig, os.Interrupt, syscall.SIGTERM)
	<-sig
	s.Shutdown()
}
