# Task 生命周期：双触发模式与委派链（设计理由）

> 契约本体见 `proto/termitaria/task/v1/task.proto` 与 `docs/contracts.md` §4。

## 两种触发模式

- **worker = 消息/turn 驱动**：轮到说话才思考（`trigger_message_id`）。
- **orchestrator = 事件驱动**：子结果到达（体现为 spoke 消息）、工单逾期、spoke 停滞、
  项目启动（`system_trigger`）。orchestrator 不能等别人点名才说话——推进、催促、截停
  都是主动行为。

## 为什么 orchestrator 的思考也是 Task

统一建模让通信层零新增：orchestrator 消费 `tasks.<swarm>.<orchestrator>`，走同一个
consumer/runner 进程模型，HPA、至少一次投递、心跳重投全部复用；只是 graph 模板不同
（plan→delegate→monitor→nudge→synthesize，对比 worker 的 reason→act→observe）。
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
