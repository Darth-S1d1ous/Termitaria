// Package bus 是 NATS / JetStream 的唯一入口：连接、stream provisioning、
// Envelope 收发。所有组件（session actor / worker 网关 / memory 客户端）
// 只通过本包接触 bus，subject 分类法见 subjects.go（contracts.md §2）。
package bus

import (
	"fmt"
	"time"

	"github.com/nats-io/nats.go"
	"github.com/nats-io/nats.go/jetstream"
)

type Bus struct {
	nc *nats.Conn
	js jetstream.JetStream
}

// start connection and verify availability of jetstream
func Connect(url string) (*Bus, error) {
	nc, err := nats.Connect(url,
	    nats.Name("termitaria-control-plane"),
		nats.MaxReconnects(-1), // infinite reconnects
		nats.ReconnectWait(500 * time.Millisecond),
	)
	if err != nil {
		return nil, fmt.Errorf("nats connect: %w", err)
	}

	js, err := jetstream.New(nc)
	if err != nil {
		nc.Close()
		return nil, fmt.Errorf("jetstream init: %w", err)
	}

	return &Bus{nc: nc, js: js}, nil
}

func (b *Bus) Close() {
	_ = b.nc.Drain()
}

func (b *Bus) Conn() *nats.Conn {return b.nc}

func (b *Bus) JetStream() jetstream.JetStream {return b.js}