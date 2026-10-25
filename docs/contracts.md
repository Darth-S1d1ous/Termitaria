# Termitaria 契约文档（L1.5）

> 契约先行：`Session` / `SwarmSpec` / `Memory` / `Graph` 四个核心接口，外加 `Task` 契约、
> 两个控制面 API、一个客户端协议，在此冻结。
> Go 控制面与 Python 智能面只依赖 `proto/` 与 `schemas/`，两侧各自演进（架构 §1、§9）。
> 本文档是契约层（L1.5）：描述接口、载体与演进规则；模块内部实现（L2）不在此列。
> 契约只冻结「是什么」；设计理由（为什么）见 [`docs/design/`](design/)（topology / task-lifecycle / ledger）。

---

## 0. 契约清单与归属矩阵

| 契约 | 定义 | 载体 | 生产方 | 消费方 |
| --- | --- | --- | --- | --- |
| `Session` / `SessionEvent` | [session.proto](../proto/termitaria/session/v1/session.proto) | JetStream `sessions.*` | Agent Runtime (Go) | Gateway、Memory、UI |
| `SwarmSpec` | [swarm.proto](../proto/termitaria/swarm/v1/swarm.proto) + [JSON Schema](../schemas/swarmspec.schema.json) | YAML 配置 → proto | 用户 | Swarm Scheduler (Go) |
| `MemoryService` | [memory.proto](../proto/termitaria/memory/v1/memory.proto) | `memory.recall` + `memory.write.episode` | Memory 服务 (Py) | Go dispatcher（预取这一个 agent）、UI（只读这一个 agent） |
| `KnowledgeGraphService` | [graph.proto](../proto/termitaria/graph/v1/graph.proto) | RPC | KG 服务 (Py) | Go dispatcher（预取这一个 project 的子图）、KG ingest、UI（只读） |
| `Task` / `TaskDelta` / `TaskResult` | [task.proto](../proto/termitaria/task/v1/task.proto) | JetStream `tasks.*` + core NATS | Agent Runtime (Go) | Py worker / Policy、Gateway |
| `Ledger` / `LedgerEvent` | [project.proto](../proto/termitaria/project/v1/project.proto) | JetStream `ledger.*` | 项目骨架 (Go) | dispatcher、UI |
| `SwarmService` / `SessionService` | swarm.proto / session.proto | Connect-RPC / REST | Swarm API (Go) | Client、CLI |
| Gateway 协议 | 本文档 §6 | WebSocket + JSON | Gateway (Go) | Next.js client |

**导入 DAG（无环）**：`common ← memory ← session ← swarm`，`common ← project`，`task` 依赖 `session` + `memory` + `project` + `common`。
`SessionPolicy` 由 session 包持有（运行时语义的所有者），swarm 配置引用它；`RecallDepth` 由 memory 包持有，swarm 引用它。

---

## 1. 全局约定

1. **统一信封**：bus 上所有消息以 `common.v1.Envelope` 包裹。`event_id`（ULID）是幂等/去重键；`trace_id` 贯穿 用户请求 → session → task → memory 全链路；`schema` 记录 payload 的全限定类型名。
   - 取舍：`payload` 用 `bytes + schema` 而非 `google.protobuf.Any`——跨语言零类型注册成本，消息在 NATS 上自描述，便于重放与调试。
2. **单写者原则**：每条 session 的流只能被它的 session actor 写入。agent 产生的消息不直接进 `sessions.*`——worker 产出 `TaskResult`，由 session actor 追加为 `MessageAppended`。actor 崩溃后由事件流重放重建（架构 §1）。
3. **幂等**：所有写入口接受幂等键（`event_id` / `client_msg_id`）；JetStream 消费者至少一次投递，消费方必须按幂等键去重。
4. **大对象不走 bus**：文档正文、大附件走对象存储，消息内只带 `content_ref` / `uri`。
5. **错误契约**：统一 `common.v1.Error{code, message, retryable}`；`code` 机器可读（如 `BUDGET_EXCEEDED`、`RECALL_TIMEOUT`、`POLICY_DENIED`）。
6. **时间**：一律 UTC epoch millis，字段名后缀 `_ms`。

---

## 2. NATS subject 分类法

| Subject 模式 | 消息类型 | 持久化 | 生产方 → 消费方 |
| --- | --- | --- | --- |
| `sessions.<swarm>.<session>.events` | `SessionEvent` | JetStream（可重放） | session actor → Gateway、Memory、UI |
| `tasks.<swarm>.<agent>` | `Task` | JetStream 工作队列 | Agent Runtime → Py worker（HPA 按队列深度扩缩） |
| `tasks.<swarm>.delta.<task>` | `TaskDelta` | core NATS（不持久化） | worker → Policy → Gateway → UI |
| `tasks.<swarm>.result.<task>` | `TaskResult` | core NATS | worker → session actor |
| `ledger.<swarm>.events` | `LedgerEvent` | JetStream（可重放） | 项目骨架 (Go) → dispatcher、UI |
| `memory.recall` | `RecallRequest/Response` | request-reply | Go dispatcher → Memory 服务。请求带一个 `agent_id`，只返回该 agent 的条目 |
| `memory.write.episode` | `WriteEpisodeRequest` | JetStream | distiller（session 关闭）→ Memory 服务。每个参与 agent 各一条，只写入该 agent |
| `memory.write.document` | `WriteDocumentRequest` | JetStream | 用户 / agent → KG ingest → 该 project 的图。不写入 agent 记忆 |

设计取舍：

- **`TaskDelta` 不持久化**：高频瞬时数据，持久化只会拖慢 JetStream；最终态由 `TaskResult` + `MessageAppended` 保证。
- **Recall 同步、写入异步**（架构 §6.3 原则 2）：召回在关键路径上但有 `Budget` 约束（50–200ms）；沉淀可以慢，不阻塞聊天。
- **通配订阅**：UI 的 swarm view 用 `sessions.<swarm>.>.*` 订阅整个 swarm 的拓扑变化；单频道视图只订 `sessions.<swarm>.<session>.events`。

---

## 3. 四大冻结接口

### 3.1 Session（session/v1）

两个参与者之间一条可寻址、持久的会话；同一对 agent 可并发多条（架构 §3.2）。

- 状态机：`OPEN → SUSPENDED ⇄ OPEN → CLOSED`，全部变更以 `SessionEvent` 落 `sessions.*`。
- 消息内容是 `ContentBlock` 的组合：text / tool_call / tool_result / attachment（attachment 可携带 `graph_node_id`，把聊天产物锚定进 KG）。
- `SessionPolicy`（轮转 / 轮次 / 并发上限 / 空闲超时）由 Policy 组件在每条 delta 通过时执行；`idle_timeout_ms` 由 actor 自己执行——空闲超时即自动关闭（`IDLE_TIMEOUT`），成为 memory episode 沉淀的触发源。

### 3.2 SwarmSpec（swarm/v1 + JSON Schema）

声明式图：节点（agent 类型 + 初始记忆策略）与边（允许的 session 策略）。**同一契约两种表示**：

| 表示 | 受众 | 校验 |
| --- | --- | --- |
| YAML（[schema](../schemas/swarmspec.schema.json)，[示例](../examples/swarms/autoresearch.yaml)） | 用户编写 | CI `check-jsonschema` |
| `termitaria.swarm.v1.SwarmSpec`（proto） | Scheduler 运行时消费 | `buf lint` / `buf breaking` |

两者必须同步演进——改一边必须改另一边，CI 双校验。

- `ModelPolicy` 是 hackathon 展示点：role → Nemotron 档位（Nano/Super 保响应，Ultra 保推理），路由是配置不是代码。
- `TopologyPolicy` 限制 `max_sessions_total` / `max_fanout_per_agent`，防拓扑爆炸（架构 §9）。
- **星型拓扑**：`goal` 是项目目标（一个项目 = 一个 swarm）；`AgentSpec.kind` 区分 `WORKER` / `ORCHESTRATOR`，每 swarm 恰好一个 orchestrator。只有 orchestrator 能 `delegate`。同事在 orchestrator 这一次 task 里同步跑完，不另开 task，也不另占 worker 名额。worker 不能再 `delegate`。YAML 里的 `edges` 当前运行时不拿来开会话。

### 3.3 Memory（memory/v1）

Memory Service 只读写一个 agent 的记忆。它不读、不写 Knowledge Graph，也不读其他 agent 的库。

- `Recall` 必须带 `agent_id`。按 `RecallDepth` 过滤种类，再用 `top_k` 截断。返回的 `MemoryItem` 全部属于这一个 agent。`RecallMeta.degraded = true` 表示后端不可用，退回罐装记忆。
- `WriteEpisode` 在 session 关闭时触发。每个参与 agent 在自己的库里各写一条。一条 episode 可以记下它碰过的 `graph_node_ids`，这些 id 只是指向 project 图的引用，正文仍留在该 agent 的库里。
- `WriteDocument` 的消息类型定义在本包，发到 `memory.write.document`。消费方是 KG ingest（§3.4），不是 Memory Service 的存储。

### 3.4 Graph（graph/v1）

Knowledge Graph Service 只读写一个 project 的图。一个 project 就是一个 swarm，对应一张 FalkorDB 图。图不按 agent 切开。agent 记忆里的 episode 不会变成这张图上的节点。

- 一个 project 一张 Graphiti 图。Graphiti 从正文抽出实体，每条边有它写下的名字和一句事实。调用方传来的边不进图。
- 写路径有两条。ingest 消费 `memory.write.document`。worker 的 `kg` 工具在推理时把一段文字交给 Graphiti，并等到抽取结束。
- `QuerySubgraph` 只查这张 project 图。dispatcher 在入队前把种子放进 `TaskContext.graph_node_ids`。同一接口也给 UI 做只读预览。worker 的 `kg` 查询走的是同一个 RPC。
- 属性图：`props` 是开放的 `google.protobuf.Struct`（作者、年份、摘要等）。节点/边的领域属性不进冻结契约。正文走 `content_ref`，不进总线。

---

## 4. Task 契约（task/v1）

一次推理工作项 = 自包含上下文 + 预算 + 回调 subject。

- **自包含的预取**：dispatcher 在入队前把会话窗口、这个 agent 的记忆、这个 project 的图种子放进 `TaskContext`。worker 另外可以在推理中调用 `KnowledgeGraphService` 的 `kg` 工具。
- **预算**：`Budget{max_tokens, max_cost_usd, deadline_ms}`，耗尽即 `TASK_STATUS_CANCELLED`。
- **双通道回写**：流式 `TaskDelta`（带 `seq` 可重排）走 delta subject；最终 `TaskResult` 走 result subject 给 session actor。
- `TaskUsage` 记录实际模型与 token 用量——demo 时展示模型分层与成本（架构 §7）。

**两种互斥的触发模式**（orchestrator 的思考也是 Task，走同一 `tasks.*` 队列与 consumer/runner 进程模型）：

| | worker | orchestrator |
| --- | --- | --- |
| 触发源 | 消息 / turn（`trigger_message_id`） | 用户在负责人的会话里发言。同样是一条 task |
| 图 | reason → act → observe，最多 20 轮工具 | 同一个图。`delegate` 在这次 task 里把同事跑完 |
| 上下文 | window + recalled + 预取的图种子 | 同样这些。步骤和成稿规格来自项目 JSON |

`parent_task_id`、`delegation_depth`、`system_trigger`、`TaskContext.ledger` 在 proto 里有定义。当前 dispatcher 不填写 `delegation_depth`，也不预取 `LedgerSnapshot`。SwarmSpec 里的 `ledger` 工具名在 worker 里是 `advance` 的别名，只改项目步骤，不写 `ledger.*.events`。

**同事的结果**作为 `delegate` 的工具返回交回 orchestrator，附带这次真正跑过的工具、改过的步骤和写成的文件。不经 spoke session，也不另开一条 task。

---

## 5. 控制面 API

| Service | RPC | 说明 |
| --- | --- | --- |
| `SwarmService` | `CreateSwarm` / `GetSwarm` / `StartSwarm` / `StopSwarm` | swarm 生命周期；`CreateSwarm` 接收编译前的 SwarmSpec |
| `SessionService` | `OpenSession` / `CloseSession` / `GetSession` / `ListSessions` / `AppendMessage` | session 寻址与路由；`AppendMessage` 仅供人/系统写入，agent 消息走 task 回路 |

传输：Connect-RPC（同时获得 REST + gRPC 两种绑定），Go 侧实现，Client/CLI 消费。

---

## 6. 客户端协议（Gateway WebSocket，JSON）

Client 是 TypeScript，WS 上用 JSON 信封（不套 proto）。`type` 字段是判别符：

**C → S**

```json
{ "type": "message.send", "session_id": "s_01J…", "client_msg_id": "c_9f3…",
  "content": [{ "text": "reviewer 怎么看这个假设？" }] }
{ "type": "session.open", "swarm_id": "sw_01J…",
  "participants": [{ "agent_id": "ideator" }, { "agent_id": "reviewer" }] }
{ "type": "subscribe", "channels": ["session:s_01J…", "swarm:sw_01J…:topology"] }
```

**S → C**

```json
{ "type": "message.delta", "session_id": "s_01J…", "message_id": "m_01J…", "seq": 12, "text": "这个假设" }
{ "type": "message.final", "session_id": "s_01J…", "message": { "…": "完整 Message（snake_case 同 proto）" } }
{ "type": "presence", "agent_id": "reviewer", "state": "thinking" }
{ "type": "topology.session_opened", "swarm_id": "sw_01J…", "session": { "…": "…" } }
{ "type": "topology.session_closed", "swarm_id": "sw_01J…", "session_id": "s_01J…" }
{ "type": "error", "code": "POLICY_DENIED", "message": "…", "retryable": false }
```

约定：`message.final` / `topology.*` 的负载与 proto 消息同构（snake_case 字段名），Gateway 只做 proto → JSON 的直通转换；`message.delta` 是 `TaskDelta` 的投影。presence/typing 仅经 WS，不落 bus。

---

## 7. 数据流对照（架构 §3.3 的契约视角）

一次 agent 互聊，每一步落在哪个契约上：

| 步骤（架构 §3.3） | 契约 | 载体 |
| --- | --- | --- |
| 1. Scheduler 拉起 A、B | `SwarmSpec` | YAML → proto |
| 2. A 向 B 开 session | `SessionService.OpenSession` → `SessionOpened` | Connect-RPC → `sessions.*` |
| 3. A 产生推理任务 | `Task` | `tasks.<swarm>.<agent>` |
| 4. dispatcher 预取这一个 agent 的记忆，以及这一个 project 的子图 | `MemoryService.Recall` · `KnowledgeGraphService.QuerySubgraph` | `memory.recall` · RPC |
| 5. delta 渲染 | `TaskDelta` → `message.delta` | `tasks.*.delta.*` → WS |
| 5b. 消息落流 | `TaskResult` → `MessageAppended` | result subject → `sessions.*` |
| 6a. session 关闭，每个参与 agent 各写一条 episode | `WriteEpisode` | `memory.write.episode` → 该 agent 的记忆 |
| 6b. 新文档或文献入库 | `WriteDocument` | `memory.write.document` → KG ingest → 该 project 的图 |

---

## 8. 演进规则与 CI 冻结

**规则**（v1 内）：

1. 只增不改：新字段用新编号；删字段 `reserved` 编号与名字，编号永不复用。
2. enum 只追加，零值保持 `*_UNSPECIFIED`。
3. 破坏性变更 → 开 `v2` package，双侧并行一段时间再下线 v1。
4. SwarmSpec 的 YAML schema 与 proto 同步演进（§3.2）。

**CI**（架构 §9「契约漂移」对策）：

```bash
buf lint                                          # 风格门禁
buf breaking --against '.git#branch=main'         # 破坏性变更门禁
buf generate                                      # 生成 Go / Python 绑定（gen/）
check-jsonschema --schemafile schemas/swarmspec.schema.json examples/swarms/*.yaml
```

**工具链**：`buf.yaml`（lint STANDARD / breaking FILE）+ `buf.gen.yaml`（Go + Python 远端插件，无需本地装 protoc）。
