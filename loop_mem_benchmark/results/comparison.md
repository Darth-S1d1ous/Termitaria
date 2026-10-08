# LoCoMo 记忆对比：kuzu-memory、实体图、Poincaré 球距离

同一套 ReAct、同一批 LoCoMo 题。开源模块 kuzu-memory 对 Poincaré 的主指标胜者是 poincare：evidence hit@5 为 0.388 对 0.460，evidence recall@5 为 0.342 对 0.436。实体关系图对 Poincaré 的胜者是 graph_kuzu：hit@5 0.511 对 0.460，recall@5 0.476 对 0.436。kuzu-memory 走的是它自己的 attach_memories，语义向量检索关掉。它的实体正则认不出单独的人名，关键词图查询在库里是关的，所以这条分数主要是关键词过滤加它自己的排序。实体图才是按 MENTIONS 边和一跳 RELATES 做的图遍历，用来和庞加莱球距离对照。没有 LLM API key 时，数字是离线 ReAct 同一次 search_memory 的检索质量，不是模型答题准确率。

Open-source module versus Poincaré: winner poincare. evidence hit@5 kuzu_memory=0.388 poincare=0.460; evidence recall@5 kuzu_memory=0.342 poincare=0.436. kuzu-memory is called through remember/attach_memories with semantic search off. Its entity patterns do not pick up single given names and its keyword graph query is disabled, so this number is the library's keyword filter plus its own ranker. Structural graph versus Poincaré: winner graph_kuzu. evidence hit@5 graph_kuzu=0.511 poincare=0.460; evidence recall@5 graph_kuzu=0.476 poincare=0.436. The structural graph ranks turns by inverse mention-degree over MENTIONS edges and one RELATES hop. That is the graph-traversal comparison. Poincaré distance is the hyperbolic distance inside the open unit ball after exp_0, and it is ahead when the question paraphrases the evidence turn. On the same vectors, mean top-5 Jaccard between Poincaré rank and cosine rank is 0.330, so the ball distance is not a rename of cosine. By category: multi-hop hit kuzu_memory=0.375 graph_kuzu=0.312 poincare=0.281; temporal hit kuzu_memory=0.486 graph_kuzu=0.703 poincare=0.730; single-hop hit kuzu_memory=0.343 graph_kuzu=0.500 poincare=0.400. Offline extraction produced 165 RELATES edges and 5524 MENTIONS edges for 419 turns, so many structural-graph hits are direct entity mentions rather than multi-hop paths. Answer-string containment is secondary: many gold answers are abstractive (especially temporal dates inferred from 'yesterday' plus the session timestamp) and do not appear verbatim even in the gold turns.

## 数据

- 数据集：LoCoMo `locomo10.json`，修订 `cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc`
- SHA256：`79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4`
- 对话：conv-26（1 段）
- 问题数：139（类别 multi-hop, temporal, single-hop）
- 切块：One memory per dialogue turn. Text is '[session_date_time] speaker: text' plus an image caption when present. The same string is passed to every memory.
- k = 5
- ReAct：llm。An OpenAI-compatible client answered through the same loop. Primary metrics still score the first search_memory top-k.

## 总指标

| 系统 | evidence hit@k | evidence recall@k | oracle recall@k | answer containment | 摄取秒 | 作答秒 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| kuzu_memory | 0.388 | 0.342 | 0.999 | 0.137 | 5.4 | 516.4 |
| graph_kuzu | 0.511 | 0.476 | 0.999 | 0.173 | 20.3 | 472.9 |
| poincare | 0.460 | 0.436 | 0.999 | 0.180 | 7.7 | 464.9 |

## 按类别

| 类别 | n | kuzu_memory hit | kuzu_memory recall | graph_kuzu hit | graph_kuzu recall | poincare hit | poincare recall |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 multi-hop | 32 | 0.375 | 0.172 | 0.312 | 0.161 | 0.281 | 0.177 |
| 2 temporal | 37 | 0.486 | 0.486 | 0.703 | 0.703 | 0.730 | 0.730 |
| 4 single-hop | 70 | 0.343 | 0.343 | 0.500 | 0.500 | 0.400 | 0.400 |

## Poincaré 与余弦

- 同一向量上 top-5 Jaccard（Poincaré vs cosine）：0.330
- top-1 不相同的问题比例：0.626
- 投影后半径 min/median/max：0.355 / 0.518 / 0.737

## 图规模（每段对话单独建图，下面是写入合计）

- utterances 419，entities 1319，mentions 5524，relations 165

## 摄取差异

- **shared**：Same turns, same order, same chunk text, same k, same ReAct policy, same questions.
- **kuzu_memory**：kuzu-memory 1.12 stores each turn with remember() (metadata chunk_id) and recalls with attach_memories(strategy='auto', use_semantic_search=False). That auto path is keyword + entity + temporal + a RELATES_TO hop. MiniLM embedding writes are skipped so the score is not a second vector ranker. The library's entity regex does not extract single given names, and its HAS_KEYWORD graph query is disabled upstream, so on this corpus the public API behaves as a keyword filter plus the library's own ranker. Candidate window is 1000, above the longest conversation, because the library LIMITs before ranking.
- **graph_kuzu**：Kuzu 0.11 stores Utterance and Entity nodes plus MENTIONS and RELATES edges. Entities and SVO relations come from spaCy en_core_web_sm because Mem0 and Graphiti extraction needs an LLM, and kuzu-memory's own regex NER misses dialogue names. First-person subjects are attached to the speaker. Retrieval is Cypher: match query entities, walk one RELATES hop (skipping hubs), rank utterances by inverse mention-degree. No embedding is used. This is the graph-traversal arm of the comparison.
- **poincare**：The same turn text is encoded with sentence-transformers/all-MiniLM-L6-v2. Vectors are mean-pooled token states before the model's L2-normalize layer, then mapped by exp_0(scale * x) with scale=0.2 into the open unit ball. Ranking uses Poincaré ball distance only.

## 分歧样例

### kuzu-memory 与 Poincaré

- 仅 kuzu_memory 命中：23；仅 poincare 命中：33

#### 仅 kuzu_memory 命中

- `conv-26:11` [multi-hop] Where did Caroline move from 4 years ago? (gold D3:13, D4:3; kuzu_memory D3:13, D9:9, D17:2, D15:1, D4:2; poincare D2:10, D6:11, D3:6, D19:9, D15:5)
- `conv-26:17` [temporal] When is Caroline going to the transgender conference? (gold D5:13; kuzu_memory D5:13, D6:1, D3:22, D12:10, D9:10; poincare —)
- `conv-26:18` [multi-hop] Where has Melanie camped? (gold D6:16, D4:6, D8:32; kuzu_memory D10:13, D18:20, D18:19, D6:16, D2:7; poincare —)
- `conv-26:20` [temporal] When did Melanie go to the museum? (gold D6:4; kuzu_memory D8:11, D6:4, D19:14, D19:12, D19:11; poincare D12:2, D2:1, D4:3, D8:8, D15:14)

#### 仅 poincare 命中

- `conv-26:10` [temporal] How long has Caroline had her current group of friends for? (gold D3:13; kuzu_memory D8:27, D3:15, D3:12, D18:6, D6:11; poincare D6:11, D17:5, D3:13, D15:5, D14:13)
- `conv-26:15` [multi-hop] What activities does Melanie partake in? (gold D5:4, D9:1, D1:12, D1:18; kuzu_memory D6:8, D8:11, D12:13, D7:12, D2:13; poincare D5:4, D11:7, D2:1, D17:25, D3:6)
- `conv-26:23` [multi-hop] What books has Melanie read? (gold D7:8, D6:10; kuzu_memory D6:8, D7:10, D4:18, D6:7, D4:11; poincare D6:10, D3:6, D7:8, D2:1, D4:3)
- `conv-26:25` [temporal] When did Caroline go to the LGBTQ conference? (gold D7:1; kuzu_memory D13:7, D14:34, D1:18, D18:17, D9:2; poincare D7:1, D10:5, D9:6, D1:3, D5:13)

### 实体图与 Poincaré

- 仅 graph_kuzu 命中：30；仅 poincare 命中：23

#### 仅 graph_kuzu 命中

- `conv-26:3` [multi-hop] What did Caroline research? (gold D2:8; graph_kuzu D2:8, D17:7, D17:8, D1:1, D1:3; poincare D15:5, D14:13, D6:11, D17:19, D6:15)
- `conv-26:11` [multi-hop] Where did Caroline move from 4 years ago? (gold D3:13, D4:3; graph_kuzu D3:13, D3:17, D3:18, D8:29, D1:1; poincare D2:10, D6:11, D3:6, D19:9, D15:5)
- `conv-26:17` [temporal] When is Caroline going to the transgender conference? (gold D5:13; graph_kuzu D5:13, D1:1, D1:3, D1:4, D1:5; poincare —)
- `conv-26:20` [temporal] When did Melanie go to the museum? (gold D6:4; graph_kuzu D6:4, D9:16, D14:7, D1:1, D1:2; poincare D12:2, D2:1, D4:3, D8:8, D15:14)

#### 仅 poincare 命中

- `conv-26:10` [temporal] How long has Caroline had her current group of friends for? (gold D3:13; graph_kuzu D6:11, D10:5, D11:6, D6:12, D19:9; poincare D6:11, D17:5, D3:13, D15:5, D14:13)
- `conv-26:15` [multi-hop] What activities does Melanie partake in? (gold D5:4, D9:1, D1:12, D1:18; graph_kuzu D15:10, D1:1, D1:2, D1:4, D1:6; poincare D5:4, D11:7, D2:1, D17:25, D3:6)
- `conv-26:23` [multi-hop] What books has Melanie read? (gold D7:8, D6:10; graph_kuzu D7:9, D6:7, D4:18, D6:8, D7:10; poincare D6:10, D3:6, D7:8, D2:1, D4:3)
- `conv-26:25` [temporal] When did Caroline go to the LGBTQ conference? (gold D7:1; graph_kuzu —; poincare D7:1, D10:5, D9:6, D1:3, D5:13)
