# 图记忆 vs Poincaré 球距离

同一个 ReAct 循环、同一批 LoCoMo 多轮对话问题，比较两种可替换的 `search_memory`：

1. **Kuzu 图记忆**：把每轮对话抽成实体和关系，写入嵌入式图数据库 [Kuzu](https://kuzudb.com/)，用 Cypher 做实体匹配和一跳关系扩展。排序用的是图上的提及度和跳数，不用向量。
2. **Poincaré 记忆**：同一段文本用 `sentence-transformers/all-MiniLM-L6-v2` 编码，投影到开单位球，按 Poincaré 球距离取 top-k。距离是

   d(u, v) = arcosh(1 + 2 * ||u-v||^2 / ((1-||u||^2) * (1-||v||^2)))

没有网页界面，也不需要单独的数据库服务。

## 环境

Python 3.10+。图抽取用 spaCy `en_core_web_sm`（离线）。Mem0 / Graphiti 的图抽取要调用 LLM，这个环境没有 API key，所以实体和关系由 spaCy 依存句法产生，并在结果里写明了这个近似。

没有 `OPENAI_API_KEY` 或 `LLM_API_KEY` 时，ReAct 走确定性的离线策略：用原问题调用一次 `search_memory`，再把排名第一的记忆当作抽取式回答。主指标是这次检索的 evidence hit@k 和 evidence recall@k，不依赖付费模型。设置了 key 之后，同一个循环会改用 OpenAI 兼容的 chat completions（`OPENAI_BASE_URL`、`OPENAI_MODEL` 可选）。

## 运行

```bash
python3 -m pip install -r requirements.txt
python3 run_benchmark.py
```

CPU 上的全量切片大约要几分钟。结果写到：

- `results/comparison.md`
- `results/comparison.json`
- `results/per_question.jsonl`

## 基准

数据是仓库里的 `data/locomo10.json`（LoCoMo，修订 `cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc`，说明见 `data/SOURCE.md`）。

- 10 段对话：`conv-26`、`conv-30`、`conv-41`、`conv-42`、`conv-43`、`conv-44`、`conv-47`、`conv-48`、`conv-49`、`conv-50`
- 问题 1444 条：类别 1 multi-hop（282）、2 temporal（321）、4 single-hop（841）
- 类别编号以 LoCoMo `task_eval/evaluation.py` 为准，和论文正文里的列举顺序不同
- 不计入类别 3（开放域，要对话之外的知识）和类别 5（对抗题，答案是对话里没提过）
- 每轮对话一条记忆，两边收到的字符串相同：`[session 时间] 说话人: 正文`，有图片时附上 BLIP caption
- k = 5
- 标注里极少数 evidence 字符串是坏的（两个 id 写在一格、多一个冒号、前导零、或指向不存在的轮次）。加载时会修好能修好的 id，丢掉对不上的 id。1444 条问题都还在。

图记忆会额外用说话人 metadata 把第一人称接到说话人节点上。Poincaré 只编码上面的字符串（说话人名字已经在字符串里）。

## 测试

```bash
python3 -m pip install pytest
python3 -m pytest
```

## 结果
同一套离线 ReAct、同一批 LoCoMo 题上，Kuzu 图记忆比庞加莱球距离更好。

| 系统 | evidence hit@5 | evidence recall@5 |
| --- | ---: | ---: |
| Kuzu 图记忆 | **0.558** | **0.501** |
| Poincaré | 0.484 | 0.422 |

(larger: better)
