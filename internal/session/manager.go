package session

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/nats-io/nats.go/jetstream"
)

var ErrSessionNotFound = errors.New("session not found")

// Manager 是 sessionID → Actor 的注册表，兼管崩溃后的重放恢复。
// 它是 Supervisor 的 MVP 形态（架构 §3.1：故障隔离在 supervisor）。
type Manager struct {
	bus    *bus.Bus
	mu     sync.RWMutex
	actors map[string]*Actor
}

func NewManager(b *bus.Bus) *Manager {
	return &Manager{bus: b, actors: make(map[string]*Actor)}
}

func (m *Manager) OpenSession(ctx context.Context, swarmID string, participants []*sessionv1.Participant, policy *sessionv1.SessionPolicy, traceID string) (*sessionv1.Session, error) {
	if len(participants) != 2 {
		return nil, ErrNeedTwoParticipants
	}
	a := newActor(m.bus, m.remove)
	session, err := a.open(ctx, swarmID, participants, policy, traceID)
	if err != nil {
		return nil, err
	}
	m.mu.Lock()
	m.actors[session.GetSessionId()] = a
	m.mu.Unlock()
	return session, nil
}

func (m *Manager) AppendMessage(
	ctx context.Context,
	sessionID string,
	from *sessionv1.Participant,
	content []*sessionv1.ContentBlock,
	replyTo,
	clientMsgID,
	traceID string,
) (*sessionv1.Message, error) {
	a, err := m.getOrRecover(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	cmd := appendCmd{from: from, content: content, replyTo: replyTo, clientMsgID: clientMsgID, traceID: traceID, result: make(chan appendResult, 1)}
	a.mailbox <- cmd
	select {
	case r := <-cmd.result:
		return r.msg, r.err
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (m *Manager) CloseSession(
	ctx context.Context, sessionID, reason, traceID string,
) (*sessionv1.Session, error) {
	a, err := m.getOrRecover(ctx, sessionID)
	if err != nil {
		return nil, err
	}

	cmd := closeCmd{reason: reason, traceID: traceID, result: make(chan error, 1)}
	a.mailbox <- cmd

	select {
	case err := <-cmd.result:
		if err != nil {
			return nil, err
		}
		return m.GetSession(ctx, sessionID)
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

func (m *Manager) GetSession(ctx context.Context, sessionID string) (*sessionv1.Session, error) {
	a, err := m.getOrRecover(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	cmd := getCmd{result: make(chan *sessionv1.Session, 1)}
	a.mailbox <- cmd
	select {
	case sess := <-cmd.result:
		return sess, nil
	case <-ctx.Done():
		return nil, ctx.Err()
	}
}

// ListSessions 返回当前内存中的 actor。
// 已知 MVP 缺口：崩溃后未恢复的 session 不在列表里——持久索引（Postgres）是 slice-2 的事。
func (m *Manager) ListSessions(ctx context.Context, swarmID string, stateFilter sessionv1.SessionState) ([]*sessionv1.Session, error) {
	m.mu.RLock()
	actors := make([]*Actor, 0, len(m.actors))
	for _, a := range m.actors {
		actors = append(actors, a)
	}
	m.mu.RUnlock()
	var out []*sessionv1.Session
	for _, a := range actors {
		cmd := getCmd{result: make(chan *sessionv1.Session, 1)}
		a.mailbox <- cmd
		sess := <-cmd.result
		if swarmID != "" && sess.GetSwarmId() != swarmID {
			continue
		}
		if stateFilter != sessionv1.SessionState_SESSION_STATE_UNSPECIFIED && sess.GetState() != stateFilter {
			continue
		}
		out = append(out, sess)
	}
	return out, nil
}

// getOrRecover 命中内存直接返回；未命中则从事件流重放重建（actor 崩溃 / 进程重启后）。
func (m *Manager) getOrRecover(ctx context.Context, sessionID string) (*Actor, error) {
	m.mu.RLock()
	a, ok := m.actors[sessionID]
	m.mu.RUnlock()
	if ok {
		return a, nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	if a, ok := m.actors[sessionID]; ok { // double-check
		return a, nil
	}
	a, err := m.replay(ctx, sessionID)
	if err != nil {
		return nil, err
	}
	m.actors[sessionID] = a
	return a, nil
}

// replay 从 SESSIONS stream 读该 session 的全部事件，用 applyEvent 重建状态。
// filter 用通配段匹配 swarm：sessions.*.<sessionID>.events
func (m *Manager) replay(ctx context.Context, sessionID string) (*Actor, error) {
	cons, err := m.bus.JetStream().CreateOrUpdateConsumer(ctx, "SESSIONS", jetstream.ConsumerConfig{
		FilterSubject: bus.SessionSubject("*", sessionID),
		DeliverPolicy: jetstream.DeliverAllPolicy,
		AckPolicy:     jetstream.AckNonePolicy, // 只读重放，不需要 ack
	})
	if err != nil {
		return nil, fmt.Errorf("replay consumer: %w", err)
	}
	a := newActor(m.bus, m.remove)
	for {
		batch, err := cons.Fetch(256, jetstream.FetchMaxWait(500*time.Millisecond))
		if err != nil {
			return nil, fmt.Errorf("replay fetch: %w", err)
		}
		n := 0
		for msg := range batch.Messages() {
			var ev sessionv1.SessionEvent
			if _, err := bus.Unmarshal(msg.Data(), &ev); err != nil {
				return nil, fmt.Errorf("replay unmarshal: %w", err)
			}
			a.applyEvent(&ev)
			n++
		}
		if batch.Error() != nil {
			return nil, fmt.Errorf("replay batch: %w", batch.Error())
		}
		if n < 256 { // 读完了
			break
		}
	}
	if a.state == nil {
		return nil, ErrSessionNotFound // 事件流里没有这条 session
	}
	go a.run()
	return a, nil
}

// remove 由 actor panic 时回调：摘除注册，下次访问触发重放恢复。
func (m *Manager) remove(sessionID string) {
	m.mu.Lock()
	delete(m.actors, sessionID)
	m.mu.Unlock()
}
