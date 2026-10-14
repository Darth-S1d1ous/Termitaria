# Termitaria 用户故事（User Stories）

> 版本：v0.1（草案）
> 配套文档：`docs/SRS.md`（编号对应 FR-xx）
> 优先级：MoSCoW（Must / Should / Could / Won't this release）

---

## Epic 1：与 Swarm 对话

### US-1.1 用户向 swarm 提问并获得流式回答（Must）

**As a** 终端用户，
**I want** 在频道里发消息并 @某个 agent，看到它的回答逐 token 流出，
**So that** 我能立刻判断回答方向对不对，不用干等完整回复。

验收标准：
- 消息发出后首 token 在 0.3–1 s 内出现在时间线上。
- 流式渲染中途断开，最终完整消息仍以 `TaskResult` 为准落库。
- 对应 SRS：FR-C1、FR-C2、FR-I2。

### US-1.2 用户看到 agent 的思考状态（Must）

**As a** 终端用户，
**I want** 看到哪个 agent 正在思考、谁在线，
**So that** 我知道 swarm 现在在干什么，而不是对着静止的屏幕猜。

验收标准：
- agent 收到 task 时 presence 变为「thinking」，task 结束恢复。
- 对应 SRS：FR-C3、FR-M7。

### US-1.3 用户在消息下开线程讨论（Should）

**As a** 终端用户，
**I want** 在任意一条消息下开 thread，
**So that** 支线讨论不打断主时间线。

验收标准：
- 每条消息可开 1..n 个线程；线程内消息同样支持流式。
- 对应 SRS：FR-C4。

---

## Epic 2：Agent 之间的 Mesh 协作

### US-2.1 agent 之间自主开会讨论（Must）

**As a** 终端用户，
**I want** 提问后看到 reviewer agent 直接挑战 researcher agent 的回答（它们自己开 session），
**So that** 我拿到的是经过内部辩论的结论，而不是一家之言。

验收标准：
- 任意两个 agent 间可开 1..n 条 session，session 持久、可寻址。
- 一轮 agent ↔ agent 对话在 3–15 s 内完成。
- nextSpeaker 轮回到人时，循环自动终止，不会无限互聊。
- 对应 SRS：FR-M3、FR-M4、FR-M5。

### US-2.2 用户实时看到 swarm 拓扑（Must）

**As a** 终端用户，
**I want** 在 Swarm View 里看到 agent 节点和 session 边随对话实时开合，
**So that** 我理解 swarm 的组织方式，也能发现哪个 agent 是瓶颈。

验收标准：
- session 打开/关闭 1 s 内反映到视图。
- 对应 SRS：FR-C5。

### US-2.3 预算熔断（Must）

**As a** Swarm 设计者，
**I want** 给每个 agent 设 token 预算，超支就停，
**So that** 一次失控的互聊不会烧光 Token Factory 配额。

验收标准：
- 超预算时 task 中止并落事件，频道里可见中止原因。
- 对应 SRS：FR-M6。

### US-2.4 故障隔离（Must）

**As a** 平台运维，
**I want** 单个 agent 崩溃时其他 agent 照常工作，崩溃 agent 自动恢复且历史不丢，
**So that** swarm 整体可用性不被单点拖垮。

验收标准：
- 杀掉一个 worker，supervisor 重启它；session 从 JetStream 事件重建。
- 对应 SRS：FR-M8、§5.3。

---

## Epic 3：自定义 Swarm

### US-3.1 用配置定义 swarm（Must）

**As a** Swarm 设计者，
**I want** 写一份 `SwarmSpec`（角色、规则、允许的 session 边、记忆策略）就能拉起一个 swarm，
**So that** 同一个 harness 不改代码就能在投研和 autoresearch 之间切换。

验收标准：
- 提交 spec 后 0.5–2 s 内 5 个 agent 全部就绪。
- spec 可限制最大 session 数和单 agent 扇出。
- 对应 SRS：FR-M1、FR-M2、FR-M9。

### US-3.2 按角色分配模型档位（Must）

**As a** Swarm 设计者，
**I want** 在配置里指定「日常对话用 Nemotron Super，深度推理用 Ultra」，
**So that** 我在响应速度和推理质量之间自己做权衡，还省 credit。

验收标准：
- model router 纯配置驱动；改配置即生效，不改 worker 代码。
- 对应 SRS：FR-I4。

---

## Epic 4：知识沉淀（Knowledge Graph）

### US-4.1 上传文档变成可查询的知识（Must）

**As a** 终端用户，
**I want** 把一份 PDF/文档拖进频道，系统自动抽取主张和引用关系写进 KG，
**So that** 之后任何 agent 都能引用它，知识不会沉在聊天记录里。

验收标准：
- 上传后 2–10 s 内 KG 可查（异步，不阻塞聊天）。
- 节点含文档与 claim，边含 cites/derives/depends。
- 对应 SRS：FR-C6、FR-P2、FR-P3。

### US-4.2 agent 发现的知识自动沉淀（Should）

**As a** 终端用户，
**I want** agent 在研究过程中找到的重要文献也进 KG，
**So that** 知识跨 session、跨 swarm 复利积累。

验收标准：
- agent 经 `memory.*` 写入的节点与用户上传的同构可查。
- 对应 SRS：FR-I6、FR-P1。

### US-4.3 查看知识图（Should）

**As a** 终端用户，
**I want** 在 Memory Inspector 里点开一个 KG 节点看它的来源和引用边，
**So that** 我能验证 agent 的结论有没有依据。

验收标准：
- 只读接口；节点详情展示 claim 文本与来源文档。
- 对应 SRS：FR-C7。

---

## Epic 5：Agent 记忆（RAG）

### US-5.1 agent 记住自己的角色与经历（Must）

**As a** Swarm 设计者，
**I want** 每个 agent 有自己的 role、rules 和 episode 记忆，用普通向量检索召回，
**So that** researcher 和 risk officer 真的行为不同，而不是同一个模型换个名字。

验收标准：
- `recall(context, top_k)` 在 50–200 ms 内返回相关 episode。
- 召回流程：embed query → Qdrant 近邻检索 → （可选）LLM 重排。
- 重排超时或不可用时直接返回向量检索结果，调用方无感知。
- 对应 SRS：FR-P1、FR-P4、FR-P5、FR-P6。

### US-5.2 记忆写入有节制（Should）

**As a** 平台运维，
**I want** policy 决定什么经历值得沉淀、旧记忆怎么衰减，
**So that** 记忆库不被垃圾 episode 淹没，召回质量不随时间劣化。

验收标准：
- 写入前过 policy；衰减策略可配置。
- 对应 SRS：FR-P7。

---

## Epic 6：运维与扩展（Could，MVP 从简）

### US-6.1 按负载扩缩 worker（Must，归此处便于追踪）

**As a** 平台运维，
**I want** Python worker 按 `tasks.*` 队列深度自动扩缩，
**So that** 高峰时推理不排队，空闲时不浪费机器。

验收标准：
- worker 无状态；队列深度是扩缩信号。
- 对应 SRS：FR-I5。

### US-6.2 后台长任务跑 Serverless Jobs（Could）

**As a** 平台运维，
**I want** 文档 ingest、长研究任务跑在 Nebius Serverless Jobs 上，
**So that** 主链路资源不被批任务挤占。

备注：hackathon 加分项，非 MVP 门槛。

---

## 发布门槛故事（MVP Demo 脚本）

| # | 故事 | 对应 SRS §9 |
| --- | --- | --- |
| D1 | 现场建一个 ≥3 agent 的 swarm，发一条消息，看到至少一轮 agent 互聊的流式过程 | 验收 1 |
| D2 | 上传一份文档，10 s 内在 Inspector 看到 KG 节点与 claim 边 | 验收 2 |
| D3 | 切一份 `SwarmSpec`，同一 harness 从投研场景变成 autoresearch 场景 | 验收 3 |
| D4 | 演示中杀掉一个 worker，supervisor 自动恢复，历史不丢 | 验收 4 |
