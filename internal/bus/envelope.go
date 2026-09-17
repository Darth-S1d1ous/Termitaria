package bus

import (
	"context"
	"crypto/rand"
	"fmt"
	"time"

	"github.com/nats-io/nats.go/jetstream"
	"github.com/oklog/ulid/v2"
	"google.golang.org/protobuf/proto"

	commonv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/common/v1"
)

// NewEnvelope 把一条具体消息包进统一信封（contracts §1.1）。
//   - event_id：ULID，幂等/去重键，同时用作 JetStream Nats-Msg-Id；
//   - trace_id：调用方传入以串起全链路；空串则新生成（链路起点）；
//   - schema：payload 的全限定类型名，如 "termitaria.session.v1.SessionEvent"，
//     取自 proto 注册表，保证与消费方 Unmarshal 的解析目标一致。
func NewEnvelope(producer, traceID string, payload proto.Message) (*commonv1.Envelope, error) {
	data, err := proto.Marshal(payload)
	if err != nil {
		return nil, fmt.Errorf("marshal payload: %w", err)
	}

	eventID := ulid.MustNew(ulid.Timestamp(time.Now()), rand.Reader).String()
	if traceID == "" {
		traceID = ulid.MustNew(ulid.Timestamp(time.Now()), rand.Reader).String()
	}

	return &commonv1.Envelope{
		EventId: eventID,
		TraceId: traceID,
		OccurredAtMs: time.Now().UnixMilli(),
		Producer: producer,
		Schema: string(payload.ProtoReflect().Descriptor().FullName()),
		Payload: data,
	}, nil
}

// Publish 把消息包封后发到 JetStream 持久化 subject (sessions.* / tasks 队列 / memory.write.*)
func (b *Bus) Publish(ctx context.Context, subject, producer, traceID string, payload proto.Message) (*commonv1.Envelope, error) {
	env, err := NewEnvelope(producer, traceID, payload)
	if err != nil {
		return nil, err
	}
	data, err := proto.Marshal(env)
	if err != nil {
		return nil, fmt.Errorf("marshal envelope: %w", err)
	}
	if _, err := b.js.Publish(ctx, subject, data, jetstream.WithMsgID(env.EventId)); err != nil {
		return nil, fmt.Errorf("publish %s: %w", subject, err)
	}
	return env, nil
}

func (b *Bus) PublishCore(subject, producer, traceID string, payload proto.Message) error {
	env, err := NewEnvelope(producer, traceID, payload)
	if err != nil {
		return err
	}

	data, err := proto.Marshal(env)
	if err != nil {
		return fmt.Errorf("marshal envelope: %w", err)
	}
	return b.nc.Publish(subject, data)
}

func Unmarshal(data []byte, target proto.Message) (*commonv1.Envelope, error) {
	var env commonv1.Envelope
	if err := proto.Unmarshal(data, &env); err != nil {
		return nil, fmt.Errorf("unmarshal envelope: %w", err)
	}

	want := string(target.ProtoReflect().Descriptor().FullName())
	if env.Schema != want {
		return nil, fmt.Errorf("schema mismatch: envelope=%q target=%q", env.Schema, want)
	}
	if err := proto.Unmarshal(env.Payload, target); err != nil {
		return nil, fmt.Errorf("unmarshal payload (%s): %w", env.Schema, err)
	}
	return &env, nil
}
