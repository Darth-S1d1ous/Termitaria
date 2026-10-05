# LoCoMo 记忆对比：kuzu-memory、实体图、Poincaré 球距离

同一套 ReAct、同一批 LoCoMo 题。开源模块 kuzu-memory 对 Poincaré 的主指标胜者是 poincare：evidence hit@5 为 0.213 对 0.484，evidence recall@5 为 0.193 对 0.422。实体关系图对 Poincaré 的胜者是 graph_kuzu：hit@5 0.558 对 0.484，recall@5 0.501 对 0.422。kuzu-memory 走的是它自己的 attach_memories，语义向量检索关掉。它的实体正则认不出单独的人名，关键词图查询在库里是关的，所以这条分数主要是关键词过滤加它自己的排序。实体图才是按 MENTIONS 边和一跳 RELATES 做的图遍历，用来和庞加莱球距离对照。没有 LLM API key 时，数字是离线 ReAct 同一次 search_memory 的检索质量，不是模型答题准确率。

Open-source module versus Poincaré: winner poincare. evidence hit@5 kuzu_memory=0.213 poincare=0.484; evidence recall@5 kuzu_memory=0.193 poincare=0.422. kuzu-memory is called through remember/attach_memories with semantic search off. Its entity patterns do not pick up single given names and its keyword graph query is disabled, so this number is the library's keyword filter plus its own ranker. Structural graph versus Poincaré: winner graph_kuzu. evidence hit@5 graph_kuzu=0.558 poincare=0.484; evidence recall@5 graph_kuzu=0.501 poincare=0.422. The structural graph ranks turns by inverse mention-degree over MENTIONS edges and one RELATES hop. That is the graph-traversal comparison. Poincaré distance is the hyperbolic distance inside the open unit ball after exp_0, and it is ahead when the question paraphrases the evidence turn. On the same vectors, mean top-5 Jaccard between Poincaré rank and cosine rank is 0.335, so the ball distance is not a rename of cosine. By category: multi-hop hit kuzu_memory=0.089 graph_kuzu=0.436 poincare=0.479; temporal hit kuzu_memory=0.187 graph_kuzu=0.632 poincare=0.573; single-hop hit kuzu_memory=0.264 graph_kuzu=0.571 poincare=0.452. Offline extraction produced 2295 RELATES edges and 70229 MENTIONS edges for 5882 turns, so many structural-graph hits are direct entity mentions rather than multi-hop paths. Answer-string containment is secondary: many gold answers are abstractive (especially temporal dates inferred from 'yesterday' plus the session timestamp) and do not appear verbatim even in the gold turns.

## 数据

- 数据集：LoCoMo `locomo10.json`，修订 `cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc`
- SHA256：`79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`
- 对话：conv-26, conv-30, conv-41, conv-42, conv-43, conv-44, conv-47, conv-48, conv-49, conv-50（10 段）
- 问题数：1444（类别 multi-hop, temporal, single-hop）
- 切块：One memory per dialogue turn. Text is '[session_date_time] speaker: text' plus an image caption when present. The same string is passed to every memory.
- k = 5
- ReAct：offline。No LLM API key was set. OfflineReActClient searches once with the raw question and then emits the top memory as an extractive final answer. Primary metrics score that tool's top-k, not the extractive answer.

## 总指标

| 系统 | evidence hit@k | evidence recall@k | oracle recall@k | answer containment | 摄取秒 | 作答秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| kuzu_memory | 0.213 | 0.193 | 0.996 | 0.137 | 40.0 | 63.8 |
| graph_kuzu | 0.558 | 0.501 | 0.996 | 0.252 | 197.2 | 14.0 |
| poincare | 0.484 | 0.422 | 0.996 | 0.209 | 22.7 | 6.0 |

## 按类别

| 类别 | n | kuzu_memory hit | kuzu_memory recall | graph_kuzu hit | graph_kuzu recall | poincare hit | poincare recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 multi-hop | 282 | 0.089 | 0.033 | 0.436 | 0.213 | 0.479 | 0.232 |
| 2 temporal | 321 | 0.187 | 0.168 | 0.632 | 0.605 | 0.573 | 0.542 |
| 4 single-hop | 841 | 0.264 | 0.257 | 0.571 | 0.557 | 0.452 | 0.440 |

## Poincaré 与余弦

- 同一向量上 top-5 Jaccard（Poincaré vs cosine）：0.335
- top-1 不相同的问题比例：0.553
- 投影后半径 min/median/max：0.341 / 0.528 / 0.737

## 图规模（每段对话单独建图，下面是写入合计）

- utterances 5882，entities 15764，mentions 70229，relations 2295

## 摄取差异

- **shared**：Same turns, same order, same chunk text, same k, same ReAct policy, same questions.
- **kuzu_memory**：kuzu-memory 1.12 stores each turn with remember() (metadata chunk_id) and recalls with attach_memories(strategy='auto', use_semantic_search=False). That auto path is keyword + entity + temporal + a RELATES_TO hop. MiniLM embedding writes are skipped so the score is not a second vector ranker. The library's entity regex does not extract single given names, and its HAS_KEYWORD graph query is disabled upstream, so on this corpus the public API behaves as a keyword filter plus the library's own ranker. Candidate window is 1000, above the longest conversation, because the library LIMITs before ranking.
- **graph_kuzu**：Kuzu 0.11 stores Utterance and Entity nodes plus MENTIONS and RELATES edges. Entities and SVO relations come from spaCy en_core_web_sm because Mem0 and Graphiti extraction needs an LLM, and kuzu-memory's own regex NER misses dialogue names. First-person subjects are attached to the speaker. Retrieval is Cypher: match query entities, walk one RELATES hop (skipping hubs), rank utterances by inverse mention-degree. No embedding is used. This is the graph-traversal arm of the comparison.
- **poincare**：The same turn text is encoded with sentence-transformers/all-MiniLM-L6-v2. Vectors are mean-pooled token states before the model's L2-normalize layer, then mapped by exp_0(scale * x) with scale=0.2 into the open unit ball. Ranking uses Poincaré ball distance only.

## 分歧样例

### kuzu-memory 与 Poincaré

- 仅 kuzu_memory 命中：133；仅 poincare 命中：525

#### 仅 kuzu_memory 命中

- `conv-26:9` [temporal] When did Caroline meet up with her friends, family, and mentors? (gold D3:11; kuzu_memory D3:22, D6:7, D3:11, D11:3, D18:22; poincare D6:11, D15:7, D6:15, D17:5, D15:5)
- `conv-26:36` [temporal] When did Caroline join a mentorship program? (gold D9:2; kuzu_memory D8:18, D9:10, D18:17, D9:2, D15:13; poincare D9:4, D4:11, D7:7, D15:9, D19:9)
- `conv-26:76` [multi-hop] When did Melanie go on a hike after the roadtrip? (gold D18:17; kuzu_memory D18:17, D2:3, D18:18, D1:15, D12:16; poincare D18:1, D14:1, D12:2, D18:15, D18:3)
- `conv-26:107` [single-hop] What is Melanie's reason for getting into running? (gold D7:21; kuzu_memory D7:21, D12:5, D7:19, D9:7, D13:14; poincare D2:1, D3:6, D18:1, D7:22, D19:9)

#### 仅 poincare 命中

- `conv-26:5` [temporal] When did Melanie run a charity race? (gold D2:1; kuzu_memory D14:30, D18:17, D13:8, D11:5, D15:26; poincare D2:1, D2:2, D12:2, D3:6, D10:5)
- `conv-26:6` [temporal] When is Melanie planning on going camping? (gold D2:7; kuzu_memory D19:14, D14:3, D1:13, D11:3, D9:4; poincare D10:12, D2:7, D18:21, D4:6, D8:32)
- `conv-26:10` [temporal] How long has Caroline had her current group of friends for? (gold D3:13; kuzu_memory D3:12, D8:27, D12:15, D6:11, D6:1; poincare D6:11, D17:5, D3:13, D15:5, D6:15)
- `conv-26:12` [temporal] How long ago was Caroline's 18th birthday? (gold D4:5; kuzu_memory D3:15, D3:19, D4:7, D3:12, D16:7; poincare D11:1, D15:5, D6:11, D17:5, D4:5)

### 实体图与 Poincaré

- 仅 graph_kuzu 命中：324；仅 poincare 命中：217

#### 仅 graph_kuzu 命中

- `conv-26:3` [multi-hop] What did Caroline research? (gold D2:8; graph_kuzu D2:8, D17:7, D17:8, D1:1, D1:3; poincare D15:5, D17:19, D3:6, D6:11, D14:13)
- `conv-26:9` [temporal] When did Caroline meet up with her friends, family, and mentors? (gold D3:11; graph_kuzu D3:11, D2:10, D6:11, D19:9, D6:12; poincare D6:11, D15:7, D6:15, D17:5, D15:5)
- `conv-26:11` [multi-hop] Where did Caroline move from 4 years ago? (gold D3:13, D4:3; graph_kuzu D3:13, D3:17, D3:18, D8:29, D1:1; poincare D6:11, D15:5, D18:5, D3:6, D17:5)
- `conv-26:16` [temporal] When did Melanie sign up for a pottery class? (gold D5:4; graph_kuzu D5:4, D14:4, D5:8, D2:10, D11:5; poincare D12:2, D8:2, D5:6, D16:9, D14:4)

#### 仅 poincare 命中

- `conv-26:10` [temporal] How long has Caroline had her current group of friends for? (gold D3:13; graph_kuzu D6:11, D10:5, D11:6, D6:12, D19:9; poincare D6:11, D17:5, D3:13, D15:5, D6:15)
- `conv-26:15` [multi-hop] What activities does Melanie partake in? (gold D5:4, D9:1, D1:12, D1:18; graph_kuzu D15:10, D1:1, D1:2, D1:4, D1:6; poincare D3:6, D2:1, D17:25, D5:4, D3:20)
- `conv-26:18` [multi-hop] Where has Melanie camped? (gold D6:16, D4:6, D8:32; graph_kuzu D9:1, D16:2, D18:20, D1:1, D1:2; poincare D12:2, D10:12, D8:32, D18:21, D14:1)
- `conv-26:23` [multi-hop] What books has Melanie read? (gold D7:8, D6:10; graph_kuzu D7:9, D6:7, D4:18, D6:8, D7:10; poincare D6:10, D3:6, D7:8, D2:17, D7:10)
