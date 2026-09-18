# 星型拓扑与角色分工（设计理由）

> 契约本体见 `proto/termitaria/swarm/v1/swarm.proto` 与 `docs/contracts.md` §3.2。
> 本文只记录「为什么」；规范性的「是什么」以契约为准。

## 为什么从 mesh 改为星型

原始定位是去中心化的 agent 交流层（类微信/WhatsApp 的 mesh）。问题：自由对话无法推进
项目——没有分解、指派、验收的对话会漂移。业界走过同一条路：AutoGen 自由群聊 wandering
→ MagenticOne 加 orchestrator + task ledger；CrewAI hierarchical 的 manager 做分解/委派/验收；
LangChain 默认 supervisor/subagents。企业类比：微信推不动项目，企业靠 PM + 工单系统。
orchestrator = PM，ledger = 工单系统。

星型不是另一套基础设施，而是 mesh 之上的拓扑策略：spoke 仍是 session，事件溯源、
单写者、bus 全部保留。

## 关键决策

- **一个项目 = 一个 swarm**：root 关联免费（`swarm_id` 即委派树根 id），无需独立的
  project registry。SwarmSpec 仍是可复用模板；项目 = 模板实例 + `goal`。
- **恰好一个 orchestrator**：schema（`contains`/`minContains`/`maxContains`）与 Scheduler 双校验。
- **spoke 隐式，EdgeSpec 只剩 worker↔worker opt-in**：worker 间默认禁止直连（防止绕开
  orchestrator 的侧向协商失控）；保留显式边作为受控例外（如 reviewer↔ideator 挑战回路）。
- **`max_fanout_per_agent` 只约束 worker**：orchestrator 的扇出是它的本职工作，
  由 `max_sessions_total` 兜底。
- **subagent 不参与 mesh**：worker 的 subagent 是父 agent 的附属，进程内同步调用、
  预算计入父 task。由此形成两层委派模型：跨 agent（orchestrator→worker）走总线异步；
  agent 内（worker→subagent）进程内同步。与 A2A 的自划界线一致
  （A2A 管 agent 间通信，不管 agent 内部怎么调 sub-agent）。

## 校验执行点

星型拓扑在 `SessionService.OpenSession` 强制执行：orchestrator↔worker 隐式允许；
worker↔worker 无显式 EdgeSpec → `POLICY_DENIED`。
