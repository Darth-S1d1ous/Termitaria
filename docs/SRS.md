# Termitaria 软件需求规格说明书（SRS）

> 版本：v0.1（草案）
> 依据：`docs/architecture.md`、`docs/images/architecture-L0.d2`、`architecture-L1-mesh.d2`、`architecture-L1-client.d2`、`architecture-L1-memory.d2`
> 范围：MVP。L2 及以后的功能明确列入「不做」。

---

## 1. 引言

### 1.1 目的

本文档定义 Termitaria MVP 的功能与非功能需求，作为 Go 控制面、Python 智能面、Web 客户端三方的共同开发依据。

### 1.2 产品概述

Termitaria 是一个高度可定制的 multi-agent harness。复杂智力工作（投研、调研、autoresearch）由「专门角色 + 共享上下文」完成，而不是单个助手。系统三层结构：

- **Client Layer**：Discord 式聊天界面，人与 swarm 多方对话。
- **Agent Mesh Layer**：agent 两两开多条 session，构成 mesh 而非线性聊天。
- **Project Memory Layer**：一个 project 两套存储。Knowledge Graph 是这个 project 的一张图。Agent Memory 是每个 agent 自己的一份 RAG 记忆。两套存储互不返回对方的记录，agent 之间的记忆也互不返回。

### 1.3 定义

| 术语 | 含义 |
| --- | --- |
| Swarm | 一组 agent 及其允许会话关系的声明式配置 |
| `SwarmSpec` | 声明式图：节点（agent 类型 + 记忆策略）、边（session 策略） |
| `Session` | 两个 agent 之间一条可寻址、持久的会话；同一对可并发多条 |
| `Task` | 一次推理工作项（上下文、预算、回调 stream） |
| KG | 一个 project 一张 Graphiti 图。Graphiti 从正文抽出实体和关系。不按 agent 切开 |
| Agent Memory | 一个 agent 一份。只含该 agent 的 role、rules、principle、episode。召回不返回其他 agent 的条目，也不返回 KG 节点 |
| Episode | 一次 session 结束后写入该 agent 自己的记忆。不写入 KG，也不写入其他 agent |
| Turn | session 内一次发言权交接 |

### 1.4 参考

- `docs/architecture.md`（L1 架构）
- `docs/contracts.md`、`proto/`（冻结接口）
- `docs/design/task-lifecycle.md`、`docs/design/topology.md`、`docs/design/ledger.md`

---

## 2. 总体描述

### 2.1 用户角色

| 角色 | 描述 |
| --- | --- |
| 终端用户（Researcher / Analyst） | 在聊天界面与 swarm 协作，上传文档，查看结果 |
| Swarm 设计者 | 编写 `SwarmSpec`，定义角色、规则、拓扑、记忆策略 |
| 平台运维 | 部署 Go 控制面、Python worker、记忆服务，监控配额 |

### 2.2 运行环境

- 控制面（Go）：Gateway、Swarm API、Scheduler、Router、Agent Runtime，无状态优先，可水平扩。
- 总线：NATS JetStream，三条 stream：`sessions.*` / `tasks.*` / `memory.*`。
- 智能面（Python）：LangGraph worker（无状态）、Memory 服务。
- 存储：FalkorDB（KG）、Postgres（结构化记忆）、Qdrant（向量检索）。
- LLM：Nebius Token Factory（OpenAI 兼容），Nemotron Super / Ultra 分层。
- 客户端：Next.js 15 + React + shadcn/ui。

### 2.3 设计约束

1. 控制面（Go）不跑任何模型推理；控制面与智能面只通过消息/RPC 通信。
2. 四个契约（`Session` / `SwarmSpec` / `Memory` / `Graph`）冻结，Protobuf/JSON Schema 描述，CI 校验。
3. 所有 LLM 调用走 Token Factory，模型选择走配置的 model router。
4. MVP 单 workspace；频道 = swarm。

### 2.4 假设

- Token Factory 配额有限，需用模型分层控制消耗。
- 单机到小规模部署即可满足 MVP 并发目标（见 §5.2）。

---

## 3. 系统架构概述

```
Client (Next.js) ──WS/REST──> Control Plane (Go)
                                  │  NATS JetStream: sessions.* / tasks.* / memory.*
              ┌───────────────────┼────────────────────┐
        Agent Runtime (Go)   Intelligence (Py)    Memory (Py)
        Supervisor/Session   LangGraph workers    KG (FalkorDB)
        actors/Policy        LLM client           Agent Memory (PG + Qdrant)
```

详见 `docs/images/architecture-L1-*.png`。

---

## 4. 功能需求

### 4.1 Client Layer

| 编号 | 需求 | 优先级 |
| --- | --- | --- |
| FR-C1 | 用户可在频道（=swarm）中发送消息，@提及具体 agent | Must |
| FR-C2 | 时间线交错展示人与 agent 消息，agent 回复按 token 流式渲染 | Must |
| FR-C3 | 显示 presence：谁在线、哪个 agent 正在思考 | Must |
| FR-C4 | 用户可在消息下开 thread（每条消息 1..n 线程） | Should |
| FR-C5 | Swarm View 实时渲染 mesh：agent 为节点、活跃 session 为边，随 session 开合更新 | Must |
| FR-C6 | 用户可上传文档，触发 KG 入库 | Must |
| FR-C7 | Memory Inspector：只读查看该 project 的 KG 节点详情，以及某一个 agent 自己的记忆条目 | Should |

### 4.2 Control Plane & Agent Mesh（Go）

| 编号 | 需求 | 优先级 |
| --- | --- | --- |
| FR-M1 | 系统接受 `SwarmSpec`（声明式 YAML/JSON），Scheduler 将其编译为运行拓扑并拉起各 agent 的 supervisor | Must |
| FR-M2 | Swarm API 提供 swarm / agent / session 的 CRUD | Must |
| FR-M3 | 任意两个 agent 之间可打开 1..n 条持久、可寻址的 session；session 事件写入 `sessions.*` | Must |
| FR-M4 | Session actor 是 session 内唯一写者，事件追加写、turn 单调递增 | Must |
| FR-M5 | Dispatcher 监听 session 事件，按 nextSpeaker 规则决定是否派生推理 `Task` 到 `tasks.*`；nextSpeaker 为人时循环终止 | Must |
| FR-M6 | Policy 执行轮转、预算上限、工具 ACL；超预算时中止 task 并落事件 | Must |
| FR-M7 | Gateway 将 agent 推理 delta 经 WS 扇出给客户端，并维护 presence | Must |
| FR-M8 | agent 故障由各自 supervisor 隔离与重启，不影响其他 agent | Must |
| FR-M9 | `SwarmSpec` 可限制最大 session 数与单 agent 扇出，防止拓扑爆炸 | Should |

### 4.3 Intelligence Plane（Python）

| 编号 | 需求 | 优先级 |
| --- | --- | --- |
| FR-I1 | LangGraph worker 从 `tasks.*` 拉取任务，执行 reason → act → observe | Must |
| FR-I2 | worker 推理过程产生 delta（core NATS，不持久化）与最终 `TaskResult`（持久化到 `tasks.*.result.*`） | Must |
| FR-I3 | LLM client 统一走 Token Factory（OpenAI 兼容接口） | Must |
| FR-I4 | Model router 按角色/预算选模型档位：日常 Nemotron Super，重推理 Ultra；路由是纯配置 | Must |
| FR-I5 | worker 无状态，按 `tasks.*` 队列深度水平扩缩 | Must |
| FR-I6 | dispatcher 在入队前预取该 agent 的记忆和该 project 的 KG 种子，放进 `TaskContext`。worker 推理期间不读写 Memory Service 或 Knowledge Graph Service | Must |

### 4.4 Project Memory（Python）

| 编号 | 需求 | 优先级 |
| --- | --- | --- |
| FR-P1 | Memory Service 只服务一个 agent：`recall(agent_id, query, depth, top_k)` / `write_episode`。召回不返回其他 agent 的条目，也不返回 KG 节点 | Must |
| FR-P2 | 文档走 `memory.write.document` 进 KG ingest：chunk → embed → LLM 抽取 claim/citation → 写入该 project 的图。异步，不阻塞聊天，不写入任何 agent 的记忆 | Must |
| FR-P3 | Knowledge Graph Service 按 project 提供子图查询。一个 project 一张图。节点为 `DOCUMENT` / `PAPER` / `CLAIM` / `CONCEPT`，边为引用 / 推导 / 依赖 / 支持 / 反驳 | Must |
| FR-P4 | Agent Memory 按 `agent_id` 隔离。role、rules、principle、episode 存在 Postgres，该 agent 的 episode 向量存在 Qdrant | Must |
| FR-P5 | Agent Memory 的召回走普通 RAG：embed query → 在该 agent 的 Qdrant 集合里近邻检索 → （可选）LLM 重排。这条路径不查 KG | Must |
| FR-P6 | 该 agent 的召回延迟超预算时可跳过重排，直接返回向量检索结果，API 签名不变 | Should |
| FR-P7 | Agent Memory 的写入遵循 policy：决定沉淀什么、衰减策略。policy 不把记录写入 KG，也不写入其他 agent | Should |

---

## 5. 非功能需求

### 5.1 性能（数量级目标，非压测承诺）

| 指标 | 目标 |
| --- | --- |
| 用户 → 单 agent 问答 | 首 token 0.3–1 s；完成 2–8 s |
| agent ↔ agent 一轮 session | 3–15 s |
| 记忆召回（RAG） | 50–200 ms（向量检索 20–50 ms + 可选重排 20–80 ms） |
| 文档入库 → KG 可查 | 等到 Graphiti 抽取结束。常为数十秒，不阻塞用户那条聊天 |
| swarm 冷启动（5 agent） | 0.5–2 s |
| 消息扇出延迟（mesh 内） | p50 < 50 ms |

### 5.2 容量

- 并发活跃 session：数百～数千（瓶颈在 Python worker 推理吞吐，不在 Go）。
- WS 连接：数千，Gateway 可水平扩。
- 记忆写入：数百 episode/s。

### 5.3 可靠性

- session 事件持久化在 JetStream，actor 崩溃后可从事件重建状态。
- 控制面永不被 LLM 阻塞：一切推理走 `tasks.*` 异步。
- delta 流丢失不影响最终一致性（以 `TaskResult` 为准）。

### 5.4 可维护性 / 可演进性

- 契约漂移防护：四接口 schema CI 校验。
- 模型、拓扑、记忆策略均为配置，不改代码即可换场景（投研 ↔ autoresearch）。

### 5.5 安全（MVP 最小集）

- 工具调用受 Policy ACL 限制。
- 单 workspace，无多租户隔离要求。

---

## 6. 外部接口

| 接口 | 说明 |
| --- | --- |
| WS（Gateway ↔ Client） | 消息收发、token delta、presence、topology 事件 |
| REST / Connect-RPC（Swarm API） | swarm / agent / session CRUD；Memory Inspector 只读查询 |
| NATS JetStream | `sessions.*`（持久）、`tasks.*`（任务+结果持久，delta 不持久）、`memory.*` |
| Token Factory API | OpenAI 兼容 chat completions |
| Memory Service | 见 FR-P1。只读写一个 agent 的记忆 |
| Knowledge Graph Service | 见 FR-P3。只读写一个 project 的图 |

契约细节冻结于 `docs/contracts.md` 与 `proto/`。

---

## 7. MVP 边界

**做**：单 workspace；频道=swarm；agent 两两 1..n session；一个 project 一张 KG，入库与查询；每个 agent 单独做 RAG 召回；Token Factory 接入 + 模型分层。

**不做**：多租户与计费；WASM 工具沙箱（Rust）；Temporal 耐久执行；跨 workspace 联邦。

---

## 8. 风险与对策

| 风险 | 对策 |
| --- | --- |
| Go↔Python 契约漂移 | 四接口 Protobuf/JSON Schema 冻结，CI 校验 |
| 向量召回质量不达标 | 加 LLM 重排或调 embedding 模型，API 不变（FR-P5、FR-P6） |
| Token Factory 延迟抖动 | 模型分层 + 流式渲染，首 token 体验优先 |
| swarm 拓扑爆炸 | `SwarmSpec` 限最大 session 数 / 单 agent 扇出（FR-M9） |

---

## 9. 验收标准（MVP 发布门槛）

1. 用户现场创建一个 swarm（≥3 agent），发一条消息，看到至少一轮 agent ↔ agent session 的流式对话。
2. 上传一份文档，10 秒内能在 Memory Inspector 查到对应 KG 节点与 claim 边。
3. 同一套 harness 通过切换 `SwarmSpec` 跑通两个场景（如投研、autoresearch），无需改代码。
4. 杀掉一个 agent 的 worker 进程，supervisor 自动恢复，session 历史不丢。
