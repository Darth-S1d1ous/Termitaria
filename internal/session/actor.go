// Package session 实现 session actor：每条 session 一个 goroutine，
// 是该 session 事件流的唯一写者（contracts.md §1.2 单写者原则）。
package session

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"
	"time"

	"github.com/oklog/ulid/v2"
	"google.golang.org/protobuf/proto"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
)

const producer = "agent-runtime"

var (
	ErrSessionClosed 		= errors.New("session is closed")
	ErrSessionSuspended 	= errors.New("session is suspended")
	ErrNotYourTurn      	= errors.New("POLICY_DENIED: not this participant's turn")
	ErrNeedTwoParticipants 	= errors.New("MVP: a session requires exactly 2 participants")
)

type command interface{ apply(a *Actor) }

type appendCmd struct {
	from *sessionv1.Participant
	content []*sessionv1.ContentBlock
	replyTo string
	clientMsgID string
	traceID string
	result chan appendResult
}

type appendResult struct {
	msg *sessionv1.Message
	err error
}

func (c appendCmd) apply(a *Actor) {
	msg, err := a.append(context.Background(), c)
	c.result <- appendResult{msg, err}
}

type closeCmd struct {
	reason  string
	traceID string
	result  chan error
}

func (c closeCmd) apply(a *Actor) {
	c.result <- a.close(context.Background(), c.reason, c.traceID)
}

type getCmd struct {
	result chan *sessionv1.Session
}

func (c getCmd) apply(a *Actor) {
	c.result <- proto.Clone(a.state).(*sessionv1.Session)
}

// ----- Actor -----

type Actor struct {
	bus 		*bus.Bus
	state 		*sessionv1.Session
	messages 	map[string]*sessionv1.Message
	mailbox 	chan command
	onExit 		func(sessionID string)
}

func newActor(b *bus.Bus, onExit func(string)) *Actor {
	return &Actor{
		bus: b,
		messages: make(map[string]*sessionv1.Message),
		mailbox: make(chan command, 64),
		onExit: onExit,
	}
}

func (a *Actor) run() {
	defer func() {
		if recover() != nil {
			a.onExit(a.state.GetSessionId())
		}
	}()
	for cmd := range a.mailbox {
		cmd.apply(a)
	}
}

func (a *Actor) open(ctx context.Context, swarmID string, participants []*sessionv1.Participant, policy *sessionv1.SessionPolicy, traceID string) (*sessionv1.Session, error) {
	session := &sessionv1.Session{
		SessionId: "s_" + ulid.MustNew(ulid.Timestamp(time.Now()), rand.Reader).String(),
		SwarmId: swarmID,
		Participants: participants,
		State: sessionv1.SessionState_SESSION_STATE_OPEN,
		Policy: policy,
		OpenedAtMs: time.Now().UnixMilli(),
	}
	event := &sessionv1.SessionEvent{
		SessionId: session.SessionId,
		SwarmId: swarmID,
		Event: &sessionv1.SessionEvent_Opened{Opened: &sessionv1.SessionOpened{Session: session}},
	}

	if _, err := a.bus.Publish(ctx, bus.SessionSubject(swarmID, session.SessionId), producer, traceID, event); err != nil {
		return nil, fmt.Errorf("publish SessionOpened: %w", err)
	}
	a.applyEvent(event)
	go a.run()
	return proto.Clone(session).(*sessionv1.Session), nil
}

func (a *Actor) append(ctx context.Context, c appendCmd) (*sessionv1.Message, error) {
	switch a.state.GetState() {
	case sessionv1.SessionState_SESSION_STATE_CLOSED:
		return nil, ErrSessionClosed
	case sessionv1.SessionState_SESSION_STATE_SUSPENDED:
		return nil, ErrSessionSuspended
	}

	// 幂等去重先于策略校验：重试命中说明该消息已被接受、状态已推进，
	// 不是新的变更请求，直接返回原消息（contracts §1.3）。
	// client_msg_id 派生 message_id，重放事件流时去重表自然重建。
	msgID := "m_" + ulid.MustNew(ulid.Timestamp(time.Now()), rand.Reader).String()
	if c.clientMsgID != "" {
		msgID = "m_" + c.clientMsgID
		if existing, ok := a.messages[msgID]; ok {
			return proto.Clone(existing).(*sessionv1.Message), nil // 重发 → 返回原消息
		}
	}

	// STRICT 轮转：必须轮到发言者（Policy 组件的 MVP 形态，内联在 actor 里）
	if a.state.GetPolicy().GetTurnTaking() == sessionv1.TurnTaking_TURN_TAKING_STRICT {
		if !proto.Equal(a.expectedSpeaker(), c.from) {
			return nil, ErrNotYourTurn
		}
	}
	msg := &sessionv1.Message{
		MessageId:         msgID,
		SessionId:         a.state.GetSessionId(),
		From:              c.from,
		Content:           c.content,
		ReplyToMessageId:  c.replyTo,
		TurnIndex:         a.state.GetTurnIndex(),
		CreatedAtMs:       time.Now().UnixMilli(),
	}
	if err := a.publish(ctx, c.traceID, &sessionv1.SessionEvent{Event: &sessionv1.SessionEvent_MessageAppended{
		MessageAppended: &sessionv1.MessageAppended{Message: msg},
	}}); err != nil {
		return nil, err
	}

	// 轮转推进
	next := a.state.GetTurnIndex() + 1
	adv := &sessionv1.TurnAdvanced{TurnIndex: next}
	if a.state.GetPolicy().GetTurnTaking() == sessionv1.TurnTaking_TURN_TAKING_STRICT {
		adv.NextSpeaker = a.state.GetParticipants()[next%2] // MVP 恰好 2 人
	}
	if err := a.publish(ctx, c.traceID, &sessionv1.SessionEvent{Event: &sessionv1.SessionEvent_TurnAdvanced{TurnAdvanced: adv}}); err != nil {
		return nil, err
	}

	// max_turns 耗尽 → 挂起（不是关闭：人可决定复盘或重开）
	if mt := a.state.GetPolicy().GetMaxTurns(); mt > 0 && next >= mt {
		_ = a.publish(ctx, c.traceID, &sessionv1.SessionEvent{Event: &sessionv1.SessionEvent_Suspended{
			Suspended: &sessionv1.SessionSuspended{Reason: "MAX_TURNS_REACHED"},
		}})
	}
	return proto.Clone(msg).(*sessionv1.Message), nil
}

func (a *Actor) close(ctx context.Context, reason, traceID string) error {
	if a.state.GetState() == sessionv1.SessionState_SESSION_STATE_CLOSED {
		return nil // 幂等
	}
	return a.publish(ctx, traceID, &sessionv1.SessionEvent{Event: &sessionv1.SessionEvent_Closed{
		Closed: &sessionv1.SessionClosed{Reason: reason},
	}})
}

// publish 把事件落 JetStream，成功后应用到内存状态。
// 顺序不可颠倒：事件流是事实来源，发布失败则状态不变。
// 注意：oneof 的包装接口 isSessionEvent 在生成代码里不导出，
// 所以这里收组装好的 *SessionEvent，由本函数补上 session/swarm 标识。
func (a *Actor) publish(ctx context.Context, traceID string, event *sessionv1.SessionEvent) error {
	event.SessionId = a.state.GetSessionId()
	event.SwarmId = a.state.GetSwarmId()
	if _, err := a.bus.Publish(ctx, bus.SessionSubject(a.state.GetSwarmId(), a.state.GetSessionId()), producer, traceID, event); err != nil {
		return fmt.Errorf("publish %T: %w", event.GetEvent(), err)
	}
	a.applyEvent(event)
	return nil
}

// applyEvent 是唯一的状态变更函数——实时路径与崩溃重放共用。
func (a *Actor) applyEvent(ev *sessionv1.SessionEvent) {
	switch e := ev.GetEvent().(type) {
	case *sessionv1.SessionEvent_Opened:
		a.state = e.Opened.GetSession()
	case *sessionv1.SessionEvent_MessageAppended:
		m := e.MessageAppended.GetMessage()
		a.messages[m.GetMessageId()] = m
	case *sessionv1.SessionEvent_TurnAdvanced:
		a.state.TurnIndex = e.TurnAdvanced.GetTurnIndex()
	case *sessionv1.SessionEvent_Suspended:
		a.state.State = sessionv1.SessionState_SESSION_STATE_SUSPENDED
	case *sessionv1.SessionEvent_Closed:
		a.state.State = sessionv1.SessionState_SESSION_STATE_CLOSED
		a.state.ClosedAtMs = time.Now().UnixMilli()
	}
}

// expectedSpeaker 返回 STRICT 轮转下当前应发言的参与者。
func (a *Actor) expectedSpeaker() *sessionv1.Participant {
	ps := a.state.GetParticipants()
	if len(ps) == 0 {
		return nil
	}
	return ps[a.state.GetTurnIndex()%uint32(len(ps))]
}