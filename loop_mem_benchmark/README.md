# 图记忆 vs Poincaré 球距离

同一个 ReAct 循环、同一批 LoCoMo 多轮对话问题，比较三种可替换的 `search_memory`：

1. **kuzu-memory**（开源图记忆，[kuzu-memory](https://github.com/bobmatnyc/kuzu-memory)）：每轮对话用 `remember()` 写进嵌入式 Kuzu，召回用 `attach_memories(strategy="auto", use_semantic_search=False)`。语义向量不参与排序。这个库的实体正则是对着产品名和「名+姓」写的，认不出对话里单独的人名；它的 HAS_KEYWORD 图查询在库里是关掉的。所以在这批数据上，公共 API 实际是关键词过滤加上它自己的排序。候选窗口取 1000，大于最长的一段对话，因为库在排序前就按新近程度做了 LIMIT。
2. **实体关系图**（Kuzu + spaCy）：把每轮对话抽成实体和关系，用 Cypher 做实体匹配和一跳关系扩展，按提及度倒数排序，不用向量。Mem0 / Graphiti 的图抽取要调用 LLM，这个环境没有 API key，所以实体和关系由 spaCy 依存句法产生。这一路才是和庞加莱球距离对照的图遍历。
3. **Poincaré 记忆**：同一段文本用 `sentence-transformers/all-MiniLM-L6-v2` 编码（取 L2 归一化之前的 mean-pool），用原点指数映射投进开单位球，按 Poincaré 球距离取 top-k。距离是

   d(u, v) = arcosh(1 + 2 * ||u-v||^2 / ((1-||u||^2) * (1-||v||^2)))

这里的「庞加莱球面」按 Termitaria 记忆设计里的 Poincaré ball 来做：点在开单位球内部，距离是双曲距离。球面上的大圆距离会和余弦单调等价，不能单独反映半径。

没有网页界面，也不需要单独的数据库服务。

## 环境

Python 3.11 或以上。`numpy==2.4.4` 和 `kuzu-memory` 都不支持 3.10。图抽取用 spaCy `en_core_web_sm`（离线）。系统自带的 `python3` 如果是 3.10，先开一个 3.11 环境再安装：

```bash
conda create -n termitaria python=3.11 -y
conda activate termitaria
python -m pip install -r requirements.txt
```

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

只跑前 N 段对话：`python3 run_benchmark.py --conversations N`。

## 基准

数据是仓库里的 `data/locomo10.json`（LoCoMo，修订 `cbfbc1dba6bc53d00625212a0f22d55ffee7c1fc`，说明见 `data/SOURCE.md`）。

- 10 段对话：`conv-26`、`conv-30`、`conv-41`、`conv-42`、`conv-43`、`conv-44`、`conv-47`、`conv-48`、`conv-49`、`conv-50`
- 问题 1444 条：类别 1 multi-hop（282）、2 temporal（321）、4 single-hop（841）
- 类别编号以 LoCoMo `task_eval/evaluation.py` 为准，和论文正文里的列举顺序不同
- 不计入类别 3（开放域，要对话之外的知识）和类别 5（对抗题，答案是对话里没提过）
- 每轮对话一条记忆，三边收到的字符串相同：`[session 时间] 说话人: 正文`，有图片时附上 BLIP caption
- k = 5
- 标注里极少数 evidence 字符串是坏的（两个 id 写在一格、多一个冒号、前导零、或指向不存在的轮次）。加载时会修好能修好的 id，丢掉对不上的 id。1444 条问题都还在。

实体图会额外用说话人 metadata 把第一人称接到说话人节点上。kuzu-memory 和 Poincaré 只使用上面的字符串（说话人名字已经在字符串里）。kuzu-memory 另外把 `chunk_id` 放进它的 metadata，用来对回证据轮次。

## 测试

```bash
python3 -m pip install pytest
python3 -m pytest
```

## 结果

同一套离线 ReAct、1444 题。主指标是 evidence hit@5 / recall@5，越大越好。

| 系统 | evidence hit@5 | evidence recall@5 |
| --- | ---: | ---: |
| kuzu-memory（开源模块，公共 API） | 0.213 | 0.193 |
| Kuzu 实体图（spaCy + Cypher） | **0.558** | **0.501** |
| Poincaré 球距离 | 0.484 | 0.422 |

Poincaré 高于 kuzu-memory。实体图高于 Poincaré，差距主要在单跳题和时序题；多跳题上 Poincaré 略高（0.479 对 0.436）。同一批向量上，Poincaré 排序和余弦排序的 top-5 Jaccard 是 0.335，球距离不是余弦的别名。完整表在 `results/comparison.md`。
