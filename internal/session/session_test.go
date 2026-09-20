package session_test

import (
	"context"
	"errors"
	"testing"

	sessionv1 "github.com/Darth-S1d1ous/Termitaria/gen/go/termitaria/session/v1"
	"github.com/Darth-S1d1ous/Termitaria/internal/bus"
	"github.com/Darth-S1d1ous/Termitaria/internal/session"
	"github.com/Darth-S1d1ous/Termitaria/internal/testutil"
)

func agent(id string) *sessionv1.Participant {
	return &sessionv1.Participant{Kind: &sessionv1.Participant_AgentId{AgentId: id}}
}

func text(s string) *sessionv1.ContentBlock {
	return &sessionv1.ContentBlock{Block: &sessionv1.ContentBlock_Text{Text: s}}
}

func strictPolicy() *sessionv1.SessionPolicy {
	return &sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_STRICT}
}

func newManager(t *testing.T) (*bus.Bus, *session.Manager) {
	t.Helper()
	b := testutil.NewBus(t)
	return b, session.NewManager(b)
}

func open(t *testing.T, m *session.Manager, policy *sessionv1.SessionPolicy) *sessionv1.Session {
	t.Helper()
	sess, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{agent("a"), agent("b")}, policy, "")
	if err != nil {
		t.Fatalf("OpenSession: %v", err)
	}
	return sess
}

func appendMsg(t *testing.T, m *session.Manager, sessionID string, from *sessionv1.Participant, clientMsgID string) (*sessionv1.Message, error) {
	t.Helper()
	return m.AppendMessage(context.Background(), sessionID, from,
		[]*sessionv1.ContentBlock{text("hello")}, "", clientMsgID, "")
}

// MVP 约束：恰好 2 个参与者。
func TestOpenSessionRequiresTwoParticipants(t *testing.T) {
	_, m := newManager(t)
	_, err := m.OpenSession(context.Background(), "sw1",
		[]*sessionv1.Participant{agent("a")}, strictPolicy(), "")
	if !errors.Is(err, session.ErrNeedTwoParticipants) {
		t.Fatalf("err = %v, want ErrNeedTwoParticipants", err)
	}
}

// STRICT 轮转：a → b → a，乱序发言被 Policy 拒绝。
func TestStrictTurnTaking(t *testing.T) {
	_, m := newManager(t)
	sess := open(t, m, strictPolicy())

	if _, err := appendMsg(t, m, sess.SessionId, agent("a"), ""); err != nil {
		t.Fatalf("a 首发应成功: %v", err)
	}
	if _, err := appendMsg(t, m, sess.SessionId, agent("a"), ""); !errors.Is(err, session.ErrNotYourTurn) {
		t.Fatalf("a 连发应被拒绝, err = %v", err)
	}
	if _, err := appendMsg(t, m, sess.SessionId, agent("b"), ""); err != nil {
		t.Fatalf("b 接话应成功: %v", err)
	}

	got, err := m.GetSession(context.Background(), sess.SessionId)
	if err != nil {
		t.Fatalf("GetSession: %v", err)
	}
	if got.GetTurnIndex() != 2 {
		t.Errorf("turn_index = %d, want 2", got.GetTurnIndex())
	}
}

// FREE 轮转：任意顺序都合法，仅受预算约束（MVP 无预算执行）。
func TestFreeTurnTaking(t *testing.T) {
	_, m := newManager(t)
	sess := open(t, m, &sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_FREE})

	for _, who := range []*sessionv1.Participant{agent("a"), agent("a"), agent("b")} {
		if _, err := appendMsg(t, m, sess.SessionId, who, ""); err != nil {
			t.Fatalf("FREE 模式下发言应成功: %v", err)
		}
	}
	got, _ := m.GetSession(context.Background(), sess.SessionId)
	if got.GetTurnIndex() != 3 {
		t.Errorf("turn_index = %d, want 3", got.GetTurnIndex())
	}
}

// 幂等：同一 client_msg_id 重发返回原消息，turn 只推进一次（contracts §1.3）。
func TestAppendIdempotentClientMsgID(t *testing.T) {
	_, m := newManager(t)
	sess := open(t, m, strictPolicy())

	m1, err := appendMsg(t, m, sess.SessionId, agent("a"), "c1")
	if err != nil {
		t.Fatalf("首次发送: %v", err)
	}
	m2, err := appendMsg(t, m, sess.SessionId, agent("a"), "c1")
	if err != nil {
		t.Fatalf("重发不应报错: %v", err)
	}
	if m1.GetMessageId() != m2.GetMessageId() {
		t.Errorf("重发应返回原消息: %q vs %q", m1.GetMessageId(), m2.GetMessageId())
	}
	got, _ := m.GetSession(context.Background(), sess.SessionId)
	if got.GetTurnIndex() != 1 {
		t.Errorf("turn_index = %d, want 1（重发不应推进轮转）", got.GetTurnIndex())
	}
}

// max_turns 耗尽 → 挂起；挂起后拒绝写入。
func TestMaxTurnsSuspends(t *testing.T) {
	_, m := newManager(t)
	policy := strictPolicy()
	policy.MaxTurns = 2
	sess := open(t, m, policy)

	if _, err := appendMsg(t, m, sess.SessionId, agent("a"), ""); err != nil {
		t.Fatalf("turn 1: %v", err)
	}
	if _, err := appendMsg(t, m, sess.SessionId, agent("b"), ""); err != nil {
		t.Fatalf("turn 2: %v", err)
	}
	got, _ := m.GetSession(context.Background(), sess.SessionId)
	if got.GetState() != sessionv1.SessionState_SESSION_STATE_SUSPENDED {
		t.Fatalf("state = %v, want SUSPENDED", got.GetState())
	}
	if _, err := appendMsg(t, m, sess.SessionId, agent("a"), ""); !errors.Is(err, session.ErrSessionSuspended) {
		t.Fatalf("挂起后写入应拒绝, err = %v", err)
	}
}

// 关闭是终态且幂等；关闭后拒绝写入。
func TestCloseSession(t *testing.T) {
	_, m := newManager(t)
	sess := open(t, m, strictPolicy())

	closed, err := m.CloseSession(context.Background(), sess.SessionId, "done", "")
	if err != nil {
		t.Fatalf("CloseSession: %v", err)
	}
	if closed.GetState() != sessionv1.SessionState_SESSION_STATE_CLOSED {
		t.Errorf("state = %v, want CLOSED", closed.GetState())
	}
	if closed.GetClosedAtMs() == 0 {
		t.Error("closed_at_ms 应被设置")
	}
	if _, err := appendMsg(t, m, sess.SessionId, agent("a"), ""); !errors.Is(err, session.ErrSessionClosed) {
		t.Errorf("关闭后写入应拒绝, err = %v", err)
	}
	if _, err := m.CloseSession(context.Background(), sess.SessionId, "again", ""); err != nil {
		t.Errorf("重复关闭应幂等: %v", err)
	}
}

func TestGetSessionNotFound(t *testing.T) {
	_, m := newManager(t)
	_, err := m.GetSession(context.Background(), "s_nonexistent")
	if !errors.Is(err, session.ErrSessionNotFound) {
		t.Fatalf("err = %v, want ErrSessionNotFound", err)
	}
}

// 崩溃恢复：新 Manager（内存为空）从事件流重放重建状态；
// 重放后幂等表也重建——同一 client_msg_id 仍返回原消息。
func TestReplayRecovery(t *testing.T) {
	b, m1 := newManager(t)
	sess := open(t, m1, strictPolicy())
	m1msg, err := appendMsg(t, m1, sess.SessionId, agent("a"), "c1")
	if err != nil {
		t.Fatalf("append: %v", err)
	}

	m2 := session.NewManager(b) // 模拟进程重启：内存状态全丢
	restored, err := m2.GetSession(context.Background(), sess.SessionId)
	if err != nil {
		t.Fatalf("重放恢复: %v", err)
	}
	if restored.GetTurnIndex() != 1 {
		t.Errorf("重放后 turn_index = %d, want 1", restored.GetTurnIndex())
	}
	if restored.GetState() != sessionv1.SessionState_SESSION_STATE_OPEN {
		t.Errorf("重放后 state = %v, want OPEN", restored.GetState())
	}

	m2msg, err := appendMsg(t, m2, sess.SessionId, agent("a"), "c1")
	if err != nil {
		t.Fatalf("恢复后重发不应报错: %v", err)
	}
	if m2msg.GetMessageId() != m1msg.GetMessageId() {
		t.Errorf("恢复后重发应返回原消息: %q vs %q", m2msg.GetMessageId(), m1msg.GetMessageId())
	}
	restored, _ = m2.GetSession(context.Background(), sess.SessionId)
	if restored.GetTurnIndex() != 1 {
		t.Errorf("重发后 turn_index = %d, want 1", restored.GetTurnIndex())
	}
}

// ListSessions 按 swarm / state 过滤。
func TestListSessions(t *testing.T) {
	_, m := newManager(t)
	s1 := open(t, m, strictPolicy())
	if _, err := m.OpenSession(context.Background(), "sw2",
		[]*sessionv1.Participant{agent("a"), agent("b")}, strictPolicy(), ""); err != nil {
		t.Fatalf("OpenSession sw2: %v", err)
	}
	if _, err := m.CloseSession(context.Background(), s1.SessionId, "done", ""); err != nil {
		t.Fatalf("CloseSession: %v", err)
	}

	all, err := m.ListSessions(context.Background(), "", sessionv1.SessionState_SESSION_STATE_UNSPECIFIED)
	if err != nil || len(all) != 2 {
		t.Fatalf("list all = %d, err = %v; want 2", len(all), err)
	}
	sw2, _ := m.ListSessions(context.Background(), "sw2", sessionv1.SessionState_SESSION_STATE_UNSPECIFIED)
	if len(sw2) != 1 {
		t.Errorf("list sw2 = %d, want 1", len(sw2))
	}
	closed, _ := m.ListSessions(context.Background(), "", sessionv1.SessionState_SESSION_STATE_CLOSED)
	if len(closed) != 1 || closed[0].GetSessionId() != s1.SessionId {
		t.Errorf("list CLOSED = %v, want [%s]", closed, s1.SessionId)
	}
}

// Snapshot：窗口按追加序、limit 取尾部；session 状态一并返回。
func TestSnapshotWindow(t *testing.T) {
	_, m := newManager(t)
	sess := open(t, m, &sessionv1.SessionPolicy{TurnTaking: sessionv1.TurnTaking_TURN_TAKING_FREE})

	var ids []string
	for _, who := range []*sessionv1.Participant{agent("a"), agent("a"), agent("b")} {
		msg, err := appendMsg(t, m, sess.SessionId, who, "")
		if err != nil {
			t.Fatalf("append: %v", err)
		}
		ids = append(ids, msg.GetMessageId())
	}

	snap, err := m.Snapshot(context.Background(), sess.SessionId, 2)
	if err != nil {
		t.Fatalf("Snapshot: %v", err)
	}
	if len(snap.Window) != 2 {
		t.Fatalf("window = %d 条，want 2", len(snap.Window))
	}
	if snap.Window[0].GetMessageId() != ids[1] || snap.Window[1].GetMessageId() != ids[2] {
		t.Errorf("窗口应为最后 2 条且按追加序：got %q, %q",
			snap.Window[0].GetMessageId(), snap.Window[1].GetMessageId())
	}
	if snap.Session.GetTurnIndex() != 3 {
		t.Errorf("turn_index = %d, want 3", snap.Session.GetTurnIndex())
	}
}
