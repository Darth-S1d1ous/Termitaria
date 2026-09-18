# 项目台账 Ledger（设计理由）

> 契约本体见 `proto/termitaria/project/v1/project.proto` 与 `docs/contracts.md` §4。

## 是什么

工单台账：orchestrator 推进项目的管理数据结构（分解 → 指派 → 状态 → 验收）。
事件溯源：`ledger.<swarm>.events`（JetStream 可重放）是事实来源，台账状态是 fold——
与 session 流同构。

## 单写者 = Go 项目骨架

- orchestrator（LLM）不直接写流：台账操作以 `ContentBlock.tool_call` 提议
  （tool 名约定 `ledger.*`），Go 骨架校验（预算上限、assignee 存在、状态机合法）
  通过后才落流。与 session 的单写者原则同构：LLM 产出提议，确定性组件持有写权限。
- worker 的交付经 spoke session 到达（TaskResult → MessageAppended），由骨架转写为
  `WorkItemDelivered`——worker 也永远不直接写 ledger。
- 理由：台账是项目的控制面状态，交给 LLM 即兴写会破坏可重放性与不变量。
  这也是 MagenticOne（orchestrator 持有 task ledger）与 CrewAI（manager 验证产出）
  的共同结构。

## 为什么台账快照走 TaskContext 而不是推理期查询

orchestrator 无状态，决策输入必须自包含；与 memory recall 同原则——读发生在 dispatch
之前，不在推理期间（架构 §6.3 的互补面：控制面不为 LLM 阻塞，LLM 也不为控制面阻塞）。
