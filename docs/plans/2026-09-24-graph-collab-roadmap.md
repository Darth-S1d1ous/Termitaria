# Knowledge Graph 模块协作路线（2026-09-24）

> 背景：同事即将开始 Knowledge Graph 模块开发。本路线规划需要准备什么，
> 以让同事第一天就能开工、双方并行不互堵。模式复用
> [memory 协作路线](2026-09-20-memory-collab-roadmap.md)（John 做外壳 stub + 同事填内核）。
>
> 前置事实：契约已冻结（`graph.proto` + contracts §3.4），Go（含 Connect）/ Python（grpc）
> 绑定已生成；FalkorDB 已在 compose（6379 Redis 协议 + Cypher，3000 Browser）；
> 写路径入口 `memory.write.document` 已在 MEMORY stream，memory 服务里有落盘 JSONL 的
> 观测 stub；三个 KG 锚点字段已在契约里（`TaskContext.graph_node_ids` /
> `Episode.graph_node_ids` / `Attachment.graph_node_id`）。
> **同事缺的不是契约，是可对测的参考实现和真实流量。**

## 已锁定的决策（本次讨论产出）

1. **传输走 gRPC，不走 bus**：Python 侧 `grpc.aio` server 实现
   `KnowledgeGraphService`（`graph_pb2_grpc` 已生成）；Go 侧用
   `graphv1connect` client + `connect.WithGRPC()`。忠于契约矩阵的「RPC」载体，
   **不新增任何 stream/subject**。备选（与 `memory.recall` 对称的 NATS
   request-reply）违背冻结契约，排除。
2. **单一写路径：只有 ingest pipeline 写 KG**。agent 推理中发现的文献也发
   `memory.write.document` 异步入库——与「recall before dispatch, not during
   inference」对称（write after, not during）。worker 对 KG 只读。
3. **幂等靠确定性 node_id**：RPC 写不走 bus envelope（没有 `event_id` 去重键），
   ingest 重试必须安全——claim 节点 id 由 `hash(doc_id + chunk_index)` 派生；
   `node_id` 留空 = 服务端分配。
4. **读路径种子来源**：dispatch 前从两处收集 `graph_node_ids`——
   (a) window 消息的 `attachment.graph_node_id`；(b) recall 回来的
   `episode.graph_node_ids`。(b) 依赖 memory 路线 P2（dispatcher recall 预取，
   目前未落地，`dispatcher.go` 里 `recalled` 仍为空）——两件事在同一处代码，合并落地。
5. **ingest 归 KG 模块**：memory 路线曾把 ingest pipeline 划给 memory 同事；
   其产出物是 KG 写入，正式归 KG。memory 服务里的 document spool consumer
   是 P1 观测 stub，KG ingest 落地后退役。

## 分工边界

| 方 | 拥有 |
| --- | --- |
| 同事 | KG 服务内核（FalkorDB schema / 索引 / Cypher / 子图遍历）、ingest 内核（chunk → embed → LLM 抽 claim/citation → Upsert）、`props` 属性约定 |
| John | `services/graph/` 骨架（RPC stub server + ingest stub + client）、conformance 测试、dispatcher 种子预取、worker prompt 接线、联调/重放脚本 |

与 memory 的一个差异：**worker 这次要改代码**——`build_prompt` 目前完全忽略
`graph_node_ids`，需要加 KG 上下文段（决策 4 的读路径终点）。

## P0 · 契约 kickoff（半天，与同事一起）

过 `graph.proto` + contracts §3.4，对齐议题：

- [ ] 传输确认（决策 1：gRPC；Go 用 Connect client + `WithGRPC()`）
- [ ] Upsert 幂等与 node_id 分配约定（决策 3）
- [ ] `QuerySubgraph` 语义细节：`depth=0` 只回种子；`max_nodes` 截断按 BFS 顺序
      保证确定性；遍历方向（`CITES` 有向，双向还是出向？）
- [ ] `props` 开放 Struct 的约定键（PAPER: `authors/year/abstract`；
      CLAIM: `doc_id/span`）——写约定文档，不进冻结契约
- [ ] 错误语义：`GetNode` 未命中 → 错误码（如 `GRAPH_NODE_NOT_FOUND`，进 common
      Error 约定）还是空响应
- [ ] `Episode.graph_node_ids` 谁填：distiller（memory 侧）沉淀时关联 KG 节点，
      可能需要「按 uri 反查节点」——确认 `GetNode` 够不够（跨模块触点）
- [ ] 联调里程碑定义：什么叫「读通」「写通」

## P1 · `services/graph/` 骨架 = stub server + 同事的起点（约 1 天）

John 建包，**按「同事将接手填充内核」的标准写**（同 memory P1 标准）：

- [ ] `services/graph/config.py`：env 配置（`NATS_URL` / `GRAPH_GRPC_ADDR` /
      `FALKORDB_URL` 占位，风格对齐 `services/worker/config.py`）
- [ ] `services/graph/server.py`：`grpc.aio` 实现 4 个 RPC，内存图 + BFS 子图查询
- [ ] `services/graph/canned.py`：罐装图数据覆盖 4 种 NodeKind × 5 种 EdgeKind，
      让 `QuerySubgraph` 的 depth/edge_kinds/max_nodes 过滤语义可测
- [ ] `services/graph/ingest.py`（stub）：独立 durable 订阅 `memory.write.document`，
      落盘 JSONL + `TODO(colleague)` 标出 chunk / embed / extract / upsert 四步填实位置
- [ ] `services/graph/client.py`：worker 用的薄客户端；KG 不可达 → 空结果降级，
      不阻塞推理（架构 §6.3）
- [ ] 顺手把 `envelope` 从 `services.worker` 上移到 `services/common/`
      （memory 路线已记的 TODO，graph 是第三个使用方）
- [ ] 离线 pytest：4 个 RPC 往返、BFS/过滤语义、ingest 落盘观测——
      **这套测试即同事的 conformance suite，换 FalkorDB 真实现后必须仍全绿**

验收：stub server 起来后，脚本能 `UpsertNode` + `QuerySubgraph` 拿到罐装子图；
向 `memory.write.document` 发消息能看到 ingest 落盘。

## P2 · 读路径接线（约 1 天，John）

- [ ] dispatcher：补 memory P2 的 recall 预取 + KG 种子收集
      （window attachments + `episode.graph_node_ids`）→ 填 `TaskContext.graph_node_ids`；
      超时/失败降级为空，**不阻塞 dispatch**（架构 §6.3）
- [ ] worker：`build_prompt` 增加 KG 上下文段——`client.QuerySubgraph(seeds, depth=1)`
      把节点 title/摘要注入 prompt；client 降级时跳过
- [ ] 单测：mock 种子 → Task 的 `context.graph_node_ids` 被填充；
      worker 离线测试带罐装 KG client

验收（E2E）：消息带 `attachment.graph_node_id` → worker prompt 里出现罐装节点标题。
**此刻同事的 QuerySubgraph 有了真实流量。**

## P3 · 同事填内核（同事主导，John 支持）

- [ ] 同事：FalkorDB schema/索引 + 4 RPC 真实现（P1 conformance 测试守门）；
      ingest 真实现（chunk → embed → LLM 抽 claim/citation → Upsert）
- [ ] John：样例文档 + natsdev 重放脚本（造 `WriteDocumentRequest` 供反复联调）；
      FalkorDB Browser（localhost:3000）演示脚本

## P4 · UI 只读路径（内核落地后）

- [ ] Go Gateway 用 `graphv1connect` client 代理 `GetNode` / `QuerySubgraph` →
      Memory Inspector KG 预览（REST 只读，架构 §2）
- [ ] 演示锚点：FalkorDB Browser 现场看子图增长——呼应 hackathon 评审点
      「知识跨 session 沉淀到 KG」（架构 §7）

## 时序

```
P0（半天）→ P1（1 天，John）─┬─→ P2（1 天，John，含 memory P2 补课）→ P4
                             └─→ 同事接手内核 + ingest（并行，conformance 测试守门）
```

同事从 P1 完成起即可全速并行；P2 完成后他有真实 QuerySubgraph 流量；
P3 联调依赖 John 的重放脚本。唯一外部依赖：P2 的 episode 种子收集依赖
memory P2（recall 预取，未落地），合并做掉。
