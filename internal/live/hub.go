// Package live 是 gateway 和 dispatcher 之间的进程内扇出。
// presence / delta 不进 JetStream。订阅方慢就丢帧，最终内容以 message.final 为准。
package live

import "encoding/json"

type TaskMeta struct {
	TaskID    string
	SessionID string
	AgentID   string
	SwarmID   string
}

type Hub struct {
	mu       chan struct{}
	tasks    map[string]TaskMeta
	inflight map[string]int
	presence map[string]string
	subs     map[chan []byte]string // chan → swarm；"*" 收全部（presence）
}

func New() *Hub {
	return &Hub{
		mu:       make(chan struct{}, 1),
		tasks:    map[string]TaskMeta{},
		inflight: map[string]int{},
		presence: map[string]string{},
		subs:     map[chan []byte]string{},
	}
}

func (h *Hub) lock()   { h.mu <- struct{}{} }
func (h *Hub) unlock() { <-h.mu }

// Subscribe 接收该 swarm 的消息帧，以及所有 presence 帧。
func (h *Hub) Subscribe(swarmID string) chan []byte {
	ch := make(chan []byte, 64)
	h.lock()
	h.subs[ch] = swarmID
	h.unlock()
	return ch
}

func (h *Hub) Unsubscribe(ch chan []byte) {
	h.lock()
	if _, ok := h.subs[ch]; ok {
		delete(h.subs, ch)
		close(ch)
	}
	h.unlock()
}

func (h *Hub) State(agentID string) string {
	h.lock()
	defer h.unlock()
	if state, ok := h.presence[agentID]; ok {
		return state
	}
	return "offline"
}

func (h *Hub) Task(taskID string) (TaskMeta, bool) {
	h.lock()
	defer h.unlock()
	meta, ok := h.tasks[taskID]
	return meta, ok
}

// Track 在 task 入队前调用，避免 delta 先到却对不上 session。
func (h *Hub) Track(meta TaskMeta) {
	h.lock()
	h.tasks[meta.TaskID] = meta
	h.inflight[meta.AgentID]++
	h.presence[meta.AgentID] = "thinking"
	h.unlock()
	h.broadcast("", presenceFrame(meta.AgentID, "thinking"))
}

// Finish 在 TaskResult 到达时调用。没有在途 task 时从 thinking 回到 online。
func (h *Hub) Finish(taskID string) {
	h.lock()
	meta, ok := h.tasks[taskID]
	if !ok {
		h.unlock()
		return
	}
	delete(h.tasks, taskID)
	if h.inflight[meta.AgentID] > 0 {
		h.inflight[meta.AgentID]--
	}
	next := ""
	if h.inflight[meta.AgentID] == 0 && h.presence[meta.AgentID] == "thinking" {
		h.presence[meta.AgentID] = "online"
		next = "online"
	}
	h.unlock()
	if next != "" {
		h.broadcast("", presenceFrame(meta.AgentID, next))
	}
}

// SetPresence 接受 worker 的 online / offline。思考中收到 online 不降级。
func (h *Hub) SetPresence(agentID, state string) {
	if state != "online" && state != "offline" {
		return
	}
	h.lock()
	if state == "online" && h.inflight[agentID] > 0 {
		state = "thinking"
	}
	if state == "offline" {
		h.inflight[agentID] = 0
	}
	h.presence[agentID] = state
	h.unlock()
	h.broadcast("", presenceFrame(agentID, state))
}

func (h *Hub) Broadcast(swarmID string, payload []byte) {
	h.broadcast(swarmID, payload)
}

func (h *Hub) broadcast(swarmID string, payload []byte) {
	h.lock()
	defer h.unlock()
	for ch, sub := range h.subs {
		if swarmID != "" && sub != swarmID {
			continue
		}
		select {
		case ch <- append([]byte(nil), payload...):
		default:
		}
	}
}

// MessageIDForTask 与 dispatcher 的 client_msg_id（"task:"+taskID）以及 actor 的 "m_" 前缀对齐。
func MessageIDForTask(taskID string) string {
	return "m_task:" + taskID
}

func presenceFrame(agentID, state string) []byte {
	raw, _ := json.Marshal(map[string]string{
		"type":     "presence",
		"agent_id": agentID,
		"state":    state,
	})
	return raw
}
