# 记忆检索对照

同一套 ReAct、同一批对话轮次、同一批问题，换掉 `search_memory` 的实现。余弦 RAG 是普通向量检索基线，用来对照 Poincaré 球距离。

## Agent 怎么建

一个 `ReActAgent` 拿着一份 `Memory` 和一个 `ModelClient`。`k = 5`，最多 4 步。系统提示只给一个工具 `search_memory`。

```text
用户问题
  -> client.complete(messages)
  -> 若 Action 是 search_memory：memory.retrieve(query, k=5)，把命中写回 Observation
  -> 若出现 Final Answer：停
```

没有 `OPENAI_API_KEY` 或 `LLM_API_KEY` 时，client 是 `OfflineReActClient`：用原问题搜一次，再把排名第一的记忆正文当作抽取式回答。设了 key 之后，同一个循环改用 OpenAI 兼容的 chat completions（`OPENAI_BASE_URL`、`OPENAI_MODEL` 可选，默认 `gpt-4o-mini`）。模型可以改写 query。两种 client 的主指标都只看第一次 `search_memory` 的 top-5，不看最终答案对不对。

四份记忆实现同一个接口：`add(text, metadata)`、`retrieve(query, k)`、`clear()`。`metadata["chunk_id"]` 是轮次 id，用来对标注。

1. **kuzu_memory**：开源库 [kuzu-memory](https://github.com/bobmatnyc/kuzu-memory)。`remember()` 写入，`attach_memories(strategy="auto", use_semantic_search=False)` 召回。语义向量不参与排序。实体正则认不出单独的人名，关键词图查询在库里是关的，所以在这批数据上实际是关键词过滤加上库自己的排序。
2. **graph_kuzu**：Kuzu 里的实体关系图。spaCy 抽实体和关系，Cypher 做提及匹配和一跳扩展，按提及度倒数排序。不用向量。
3. **vector_rag**：普通稠密 RAG。`sentence-transformers/all-MiniLM-L6-v2` 的 mean-pool，再做 L2 归一化（模型自己的 normalize 层），按余弦取 top-k。余弦在单位向量上就是点积。
4. **poincare**：同一段 mean-pool，不做 L2 归一化，用 `exp_0(0.2 * x)` 投进开单位球，按 Poincaré 球距离取最近的 k 条。和 vector_rag 共用一个编码器。距离是

   d(u, v) = arcosh(1 + 2 * ||u-v||^2 / ((1-||u||^2) * (1-||v||^2)))

## 输入和输出

每段对话单独建库，问完 `clear()`。一轮对话一条记忆，四边收到的字符串相同：

```text
[session 时间] 说话人: 正文
```

有图时正文后面加上 `(image: BLIP caption)`。说话人名字已经在字符串里。实体图另外用 metadata 里的 speaker 把第一人称接到说话人节点。

一道题的输入是问题字符串。检索的输出是最多 5 条 `MemoryHit`：`chunk_id`（如 `D3:13`）、原文、分数（余弦越大越好，球距离取负号后越大越好）。

跑完写三个文件：

- `results/comparison.md`：总表、分类表、分歧样例
- `results/comparison.json`：同一份结构
- `results/per_question.jsonl`：每系统每题一行，含 `retrieved_ids`、`evidence`、`evidence_hit`、`evidence_recall`

## Benchmark

数据是 LoCoMo 的 `data/locomo10.json`（snap-research/locomo，修订 `cbfbc1d`，说明见 `data/SOURCE.md`）。这是各家用的那份十段对话文件。这里的计分不是各家论文里的问答 F1。

- 全量 10 段：`conv-26`、`conv-30`、`conv-41`、`conv-42`、`conv-43`、`conv-44`、`conv-47`、`conv-48`、`conv-49`、`conv-50`
- 保留类别 1 multi-hop、2 temporal、4 single-hop，共 1444 题。编号以 LoCoMo `task_eval/evaluation.py` 为准
- 不计入类别 3（开放域，要对话之外的知识）和类别 5（对抗题，答案是对话里没提过）
- 标注里少数写坏的 evidence id 会先修好再丢掉对不上的。1444 条都还在
- `python3 run_benchmark.py --conversations N` 只跑前 N 段。`results/comparison.md` 里已有的表是加 vector_rag 之前、只跑了 `conv-26`（139 题）的结果，重跑后会被覆盖

## 评价指标

主指标只比较第一次 `search_memory` 返回的轮次 id 和标注 evidence id。k = 5。越大越好。

| 指标 | 含义 |
| --- | --- |
| evidence hit@5 | top-5 里至少有一条 gold 轮次则为 1，再对题目取平均 |
| evidence recall@5 | 捞中的 gold 条数 / 该题 gold 条数。单条证据的题上它等于 hit |
| oracle recall@5 | 排序完美时 k=5 最多能盖住的 gold 比例。接近 1 说明 k 不是瓶颈 |
| answer containment | gold 答案字符串是否出现在捞回的正文里。次要。很多标准答案是概括出来的，原文里没有这几个词 |

这不是模型答题准确率，也不是 Mem0 / Graphiti 公布的 LoCoMo F1。

## 环境

Python 3.11 或以上。`numpy==2.4.4` 和 `kuzu-memory` 都不支持 3.10。图抽取用 spaCy `en_core_web_sm`（离线）。系统自带的 `python3` 如果是 3.10，先开一个 3.11 环境再安装：

```bash
conda create -n termitaria python=3.11 -y
conda activate termitaria
python -m pip install -r requirements.txt
```

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

## 测试

```bash
python3 -m pip install pytest
python3 -m pytest
```

## 已落盘的旧结果

`results/comparison.md` 是加入余弦 RAG 之前的一次运行：只有 `conv-26`，139 题，并且当时的 ReAct 用了 LLM，query 不一定是原问题。那张表里没有 `vector_rag`。重跑 `python3 run_benchmark.py` 会覆盖它。
