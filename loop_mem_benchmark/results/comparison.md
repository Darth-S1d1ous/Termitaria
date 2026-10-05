# LoCoMo 记忆对比：Kuzu 图记忆 vs Poincaré 球距离

主指标上 Kuzu 图记忆更好：evidence hit@5 为 0.558，Poincaré 为 0.484；evidence recall@5 为 0.501 对 0.422。差距主要来自单跳题和时序题：问题里的人名和具体事件能对上图里的实体节点，按提及度倒数排序就能找到证据轮次。多跳题上 Poincaré 略高，因为问法经常换一种说法，实体字符串对不上。环境里没有 LLM API key，所以上面的数字是离线 ReAct 同一次 `search_memory` 的检索质量，不是模型答题准确率。

Winner on the primary retrieval metrics: graph_kuzu. evidence hit@5 graph=0.558 poincare=0.484; evidence recall@5 graph=0.501 poincare=0.422. The Kuzu graph did better overall, mostly on single-hop and temporal questions. Those questions repeat a person and a concrete event that the offline extractor stored as entity nodes, so inverse-degree ranking over MENTIONS edges finds the evidence turn. Poincaré distance is ahead on multi-hop questions, where the wording drifts away from any single turn. RELATES edges are real, but they are sparse next to MENTIONS, so one-hop expansion is not the main source of the gap. On the same vectors, mean top-5 Jaccard between Poincaré rank and cosine rank is 0.335, so the ball distance is not a rename of cosine. By category: multi-hop hit graph=0.436 poincare=0.479 (poincare); temporal hit graph=0.632 poincare=0.573 (graph_kuzu); single-hop hit graph=0.571 poincare=0.452 (graph_kuzu). Offline extraction produced 2295 RELATES edges and 70229 MENTIONS edges for 5882 turns, so many graph hits are direct entity mentions rather than multi-hop paths. Answer-string containment is secondary: many gold answers are abstractive (especially temporal dates inferred from 'yesterday' plus the session timestamp) and do not appear verbatim even in the gold turns.

## 数据

- 数据集：LoCoMo `locomo10.json`，修订 `cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc`
- SHA256：`79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`
- 对话：conv-26, conv-30, conv-41, conv-42, conv-43, conv-44, conv-47, conv-48, conv-49, conv-50（10 段）
- 问题数：1444（类别 multi-hop, temporal, single-hop）
- 切块：One memory per dialogue turn. Text is '[session_date_time] speaker: text' plus an image caption when present. The same string is passed to both memories.
- k = 5
- ReAct：offline。No LLM API key was set. OfflineReActClient searches once with the raw question and then emits the top memory as an extractive final answer. Primary metrics score that tool's top-k, not the extractive answer.

## 总指标

| 系统 | evidence hit@k | evidence recall@k | oracle recall@k | answer containment | 摄取秒 | 作答秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| graph_kuzu | 0.558 | 0.501 | 0.996 | 0.252 | 189.7 | 13.2 |
| poincare | 0.484 | 0.422 | 0.996 | 0.209 | 23.3 | 6.2 |

## 按类别

| 类别 | n | graph hit | poincaré hit | graph recall | poincaré recall |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 multi-hop | 282 | 0.436 | 0.479 | 0.213 | 0.232 |
| 2 temporal | 321 | 0.632 | 0.573 | 0.605 | 0.542 |
| 4 single-hop | 841 | 0.571 | 0.452 | 0.557 | 0.440 |

## Poincaré 与余弦

- 同一向量上 top-5 Jaccard（Poincaré vs cosine）：0.335
- top-1 不相同的问题比例：0.553
- 投影后半径 min/median/max：0.341 / 0.528 / 0.737

## 图规模（每段对话单独建图，下面是写入合计）

- utterances 5882，entities 15764，mentions 70229，relations 2295

## 摄取差异

- **shared**：Same turns, same order, same chunk text, same k, same ReAct policy, same questions.
- **graph_kuzu**：Kuzu 0.11 stores Utterance and Entity nodes plus MENTIONS and RELATES edges. Entities and SVO relations come from spaCy en_core_web_sm because Mem0/Graphiti extraction needs an LLM. First-person subjects are attached to the speaker. Retrieval is Cypher: match query entities, walk one RELATES hop (skipping hubs), rank utterances by inverse mention-degree. No embedding is used.
- **poincare**：The same turn text is encoded with sentence-transformers/all-MiniLM-L6-v2. Vectors are mean-pooled token states before the model's L2-normalize layer, then mapped by exp_0(scale * x) with scale=0.2 into the open unit ball. Ranking uses Poincaré ball distance only.

## 分歧样例

- 仅图命中：324；仅 Poincaré 命中：217

### 图命中、Poincaré 未命中

- `conv-26:3` [multi-hop] What did Caroline research? (gold D2:8; graph D2:8, D17:7, D17:8, D1:1, D1:3; poincaré D15:5, D17:19, D3:6, D6:11, D14:13)
- `conv-26:9` [temporal] When did Caroline meet up with her friends, family, and mentors? (gold D3:11; graph D3:11, D2:10, D6:11, D19:9, D6:12; poincaré D6:11, D15:7, D6:15, D17:5, D15:5)
- `conv-26:11` [multi-hop] Where did Caroline move from 4 years ago? (gold D3:13, D4:3; graph D3:13, D3:17, D3:18, D8:29, D1:1; poincaré D6:11, D15:5, D18:5, D3:6, D17:5)
- `conv-26:16` [temporal] When did Melanie sign up for a pottery class? (gold D5:4; graph D5:4, D14:4, D5:8, D2:10, D11:5; poincaré D12:2, D8:2, D5:6, D16:9, D14:4)

### Poincaré 命中、图未命中

- `conv-26:10` [temporal] How long has Caroline had her current group of friends for? (gold D3:13; graph D6:11, D10:5, D11:6, D6:12, D19:9; poincaré D6:11, D17:5, D3:13, D15:5, D6:15)
- `conv-26:15` [multi-hop] What activities does Melanie partake in? (gold D5:4, D9:1, D1:12, D1:18; graph D15:10, D1:1, D1:2, D1:4, D1:6; poincaré D3:6, D2:1, D17:25, D5:4, D3:20)
- `conv-26:18` [multi-hop] Where has Melanie camped? (gold D6:16, D4:6, D8:32; graph D9:1, D16:2, D18:20, D1:1, D1:2; poincaré D12:2, D10:12, D8:32, D18:21, D14:1)
- `conv-26:23` [multi-hop] What books has Melanie read? (gold D7:8, D6:10; graph D7:9, D6:7, D4:18, D6:8, D7:10; poincaré D6:10, D3:6, D7:8, D2:17, D7:10)
