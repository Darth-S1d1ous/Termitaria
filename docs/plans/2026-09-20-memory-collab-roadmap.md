# Python 侧完善与 Memory 模块协作路线（2026-09-20）

> 背景：同事即将开始 Memory 模块（记忆服务）开发。本路线规划 Python 侧需要完善什么，
> 以让同事第一天就能开工、双方并行不互堵。
>
> 前置事实：契约已冻结（`memory.proto` + contracts §2/§3.3）；基础设施已在 compose
> （NATS 含 MEMORY stream、Postgres、Qdrant、FalkorDB）；worker 的 `build_prompt`
> 已会消费 `TaskContext.recalled`。**同事缺的不是契约，是可对测的参考实现和真实流量。**

## 已锁定的决策（本次讨论产出）

1. **Episode 沉淀归 memory 服务**：记忆服务自己订阅 `sessions.*` 的 `SessionClosed`，
   按 participants fan-out 蒸馏（Episode 是 per-agent 的），自己调 LLM summarize。
   对标主流：Google ADK `add_session_to_memory` / Mem0 / Zep / LangMem background 模式
   全是「记忆层自沉淀」；推理期内自写（MemGPT 派）与我们「recall before dispatch,
   not during inference」原则冲突，排除。
2. **先全 stub 打通 memory 回路，真 LLM（Token Factory）最后接**——联调不依赖外部配额。
3. **Go dispatcher 做 recall 预取**（dispatch 前 request-reply，结果塞进 TaskContext），
   维持 worker 无状态。L1-mesh 图缺 `runtime → bus.s3` 这条边，属文档卫生项。
4. **reflection（EPISODE→PRINCIPLE 晋升，Generative Agents 模式）列入同事 backlog**——
   阈值触发、服务内部闭环，是 Poincaré 层级故事的亮点。

## 分工边界

| 方 | 拥有 |
| --- | --- |
| 同事 | Memory 服务本体：recall（Qdrant 候选 → Poincaré 精排）、存储、ingest pipeline、episode 自沉淀、reflection（backlog） |
| John | 总线集成外壳：`services/memory/` 骨架（同事接手填充内核）、Go dispatcher recall 预取、session 事件重放工具、worker LLM client |

关键结论：**worker 不需要为 memory 改任何代码**——读路径走 TaskContext（已就绪），
写路径归 memory 服务。「完善 Python 一侧」的实际工作量比预想小。

## P0 · 契约 kickoff（半天，与同事一起）

过 `memory.proto` + contracts §2/§3.3，对齐议题：

- [ ] episode 自沉淀方案确认（本计划决策 1；同事接受则 contracts §2 生产方需修正）
- [ ] `degraded` 降级语义（欧氏兜底，接口不变）、召回预算 50–200ms 的超时行为
- [ ] `Budget.deadline_ms` 语义：proto 是绝对 epoch ms，`swarmspec.schema.json` 注释
      是相对毫秒——约定由 SwarmSpec loader 入队时换算成绝对时间
- [ ] 幂等键：`event_id`（ULID）去重约定；write 路径至少一次投递
- [ ] 联调里程碑定义：什么叫「recall 通」「write 通」

## P1 · `services/memory/` 骨架 = mock server + 同事的起点（约 1 天）

John 建包，**按「同事将接手填充内核」的标准写**——总线集成这层他最熟契约：

- [ ] `services/memory/config.py`：env 配置（NATS_URL 等，风格对齐 `services/worker/config.py`）
- [ ] `services/memory/server.py`：订阅 `memory.recall`（request-reply）与
      `memory.write.episode` / `memory.write.document`（JetStream pull）；
      recall 返回罐装 `MemoryItem`（含 ROLE/RULE/PRINCIPLE 各一，带 `poincare_radius`
      与 `parent_id`，让层级字段可测）；write 落盘 JSONL 便于查看
- [ ] `services/memory/distiller.py`（stub）：订阅 `sessions.*` 的 `SessionClosed`，
      打日志——决策 1 的占位，同事填蒸馏逻辑
- [ ] 复用 `services/worker/envelope.py`（考虑上移到 `services/common/`，两包共用）
- [ ] 离线 pytest：罐装 recall 的往返、write 落盘、envelope 校验

验收：`natsdev` 或脚本发 recall 请求能拿到罐装响应；向 `memory.write.episode`
发消息能看到落盘。

## P2 · Go dispatcher recall 预取（约半天，Go 侧）

- [ ] dispatcher enqueue 前调 `memory.recall`（NATS request-reply，envelope 封装，
      timeout 取 `RecallRequest.budget`，默认 200ms）
- [ ] 超时/失败降级：`recalled` 置空 + 日志，**不阻塞 dispatch**（架构 §6.3）
- [ ] 顺手修 `deadline_ms` 换算（P0 议题 3）
- [ ] 单测：mock NATS 应答 → Task 的 `context.recalled` 被填充

验收（E2E）：P1 mock server 返回 ROLE 条目 → worker system prompt 带角色 →
stub 输出可观测变化。此刻同事的 recall 接口有了真实流量。

## P3 · Episode 自沉淀（同事主导，John 支持）

- [ ] 同事：P1 的 `distiller.py` 占位填实——SessionClosed → 按 participants fan-out
      → LLM summarize → 写库
- [ ] John：提供 session 事件样例 / natsdev 重放脚本（造一条关闭的 session 供反复联调）
- [ ] 契约修正：contracts §2 `memory.write.episode` 生产方改为「Memory 服务（自沉淀）；
      显式写入口保留」；L1-mesh 图补 `runtime → bus.s3` 边

## P4 · 真 LLM client（memory 回路通了之后）

- [ ] OpenAI 兼容 client 实现 `ModelPort.stream()`（Token Factory / Nemotron），
      env 配 key 与 base_url；`StubModel` 保留给测试
- [ ] `TaskUsage` 真实回填（cost 展示的数据源，架构 §7）

## 时序

```
P0（半天）→ P1（1 天）──┬─→ P2（半天，John）→ P4
                       └─→ 同事接手 services/memory 内核 + P3（并行）
```

同事从 P1 完成起即可全速并行；P2 完成后他有真实 recall 流量；P3 联调依赖
John 的重放脚本，不依赖 P4。
