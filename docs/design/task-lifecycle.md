# Task 生命周期：双触发模式与委派链（设计理由）

> 契约本体见 `proto/termitaria/task/v1/task.proto` 与 `docs/contracts.md` §4。

## 两种触发模式

- **worker = 消息/turn 驱动**：轮到说话才思考（`trigger_message_id`）。
- **orchestrator**：用户在负责人的会话里发言会触发负责人的 task。同事不另开 task。`delegate` 在这次 task 里把同事跑完，工具结果交回来。

## 为什么 orchestrator 的思考也是 Task

统一建模让通信层零新增：orchestrator 消费 `tasks.<swarm>.<orchestrator>`，走同一个
consumer/runner 进程模型，HPA、至少一次投递、心跳重投全部复用。负责人的 `delegate` 不另开一条 task。
唯一的新逻辑在 Go dispatcher 的触发规则。

## 委派链字段

- `parent_task_id`：因果链（≈ A2A `referenceTaskIds`），用于追踪与调试；
- `delegation_depth`：委派跳数上限，防 A→B→A 乒乓（session 的 `max_turns` 管不住 task 链）；
- `work_item_id`：台账锚点——spoke 是持久的（跨多个工单复用），task 必须显式声明服务的工单；
- root 关联免费：一个项目 = 一个 swarm，`swarm_id` 即根。

## 为什么不加 TASK_STATUS_WAITING

事件驱动续跑下，「等待」= task 正常 `COMPLETED` + 事件到达后的新 task（上下文从 session
事件流重建，事件溯源白送 checkpoint）。这与 A2A `input-required` 的续跑语义同构；
v1 内若确需显式等待态，可按契约演进规则（contracts §8）只增后补。

## 自包含上下文

worker 无状态 → `TaskContext` 必须带齐 window + recalled + KG 节点；orchestrator 同样
无状态 → 台账以 `LedgerSnapshot` 在 dispatch 前预取（与「memory recall happens before
task dispatch, not during inference」同原则）。
