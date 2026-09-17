package bus

import "fmt"

// SessionEvents 是某条 session 的事件流 subject（JetStream 持久化）。
// 生产：session actor（单写者）。消费：Gateway / Memory / UI。
func SessionSubject(swarmID, sessionID string) string {
	return fmt.Sprintf("sessions.%s.%s.events", swarmID, sessionID)
}

func SessionEventsWildcard(swarmID string) string {
	return fmt.Sprintf("sessions.%s.>", swarmID)
}

// TaskQueue 是某 agent 的推理任务工作队列 subject (JetStream WorkQueue）
func TaskQueue(swarmID, agentID string) string {
	return fmt.Sprintf("tasks.%s.%s", swarmID, agentID)
}

// TaskDelta 是流式增量 subject（core NATS，不持久化）。
func TaskDelta(swarmID, taskID string) string {
	return fmt.Sprintf("tasks.%s.delta.%s", swarmID, taskID)
}

// TaskResult 是最终结果 subject（core NATS），worker → session actor。
func TaskResult(swarmID, taskID string) string {
	return fmt.Sprintf("tasks.%s.result.%s", swarmID, taskID)
}

const MemoryRecall = "memory.recall"

const (
	MemoryWriteEpisode = "memory.write.episode"
	MemoryWriteDocument = "memory.write.document"
)