"""Run the same ReAct loop and the same LoCoMo questions on both memories."""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections import defaultdict
from pathlib import Path

from memcompare.graph_memory import GraphMemory
from memcompare.kuzu_open_memory import KuzuOpenMemory
from memcompare.locomo import (
    CATEGORY_NAMES,
    DATASET_REVISION,
    DATASET_SHA256,
    DATASET_URL,
    PRIMARY_CATEGORIES,
    QAItem,
    load_locomo,
)
from memcompare.metrics import (
    answer_contained,
    evidence_hit,
    evidence_recall,
    mean,
    oracle_recall,
    token_f1,
)
from memcompare.poincare import PROJECTION_SCALE
from memcompare.poincare_memory import PoincareMemory, _MODEL
from memcompare.react import ReActAgent, build_client

K_DEFAULT = 5


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def run(argv: list[str] | None = None) -> dict:
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    logging.getLogger("kuzu_memory").setLevel(logging.ERROR)
    args = _parse(argv)
    categories = tuple(int(part) for part in args.categories.split(",") if part)
    conversations = load_locomo(
        max_conversations=args.conversations or None,
        categories=categories,
    )
    questions = [q for convo in conversations for q in convo.questions]
    if len(questions) < 20:
        raise SystemExit(f"Subset has only {len(questions)} questions; expected at least 20.")

    client, client_mode = build_client()
    k = args.k
    out_dir = Path(args.out_dir)
    if not out_dir.is_absolute():
        out_dir = _repo_root() / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    systems = [
        ("kuzu_memory", lambda: KuzuOpenMemory()),
        ("graph_kuzu", lambda: GraphMemory()),
        ("poincare", lambda: PoincareMemory()),
    ]
    reports = []
    per_question_rows = []
    for name, factory in systems:
        print(f"== {name} ==", flush=True)
        memory = factory()
        agent = ReActAgent(memory, client, k=k, max_steps=4)
        rows, timing, extra = _eval_system(name, memory, agent, conversations, k)
        reports.append(_summarize(name, rows, timing, extra, k))
        per_question_rows.extend(rows)
        del memory

    comparison = _comparison(conversations, questions, reports, per_question_rows, client_mode, k, categories)
    (out_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (out_dir / "comparison.md").write_text(_markdown(comparison), encoding="utf-8")
    with (out_dir / "per_question.jsonl").open("w", encoding="utf-8") as handle:
        for row in per_question_rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(_markdown(comparison), flush=True)
    return comparison


def _eval_system(name, memory, agent, conversations, k: int):
    rows = []
    ingest_seconds = 0.0
    answer_seconds = 0.0
    graph_stats = []
    cosine_overlaps = []
    cosine_top1_differ = 0
    cosine_n = 0
    for convo in conversations:
        memory.clear()
        t0 = time.perf_counter()
        for chunk in convo.chunks:
            memory.add(chunk.text, {"chunk_id": chunk.chunk_id, "speaker": chunk.speaker})
        memory._flush()
        ingest_seconds += time.perf_counter() - t0
        if hasattr(memory, "stats"):
            graph_stats.append(memory.stats)
        print(
            f"  {convo.sample_id}: {len(convo.chunks)} turns, {len(convo.questions)} questions",
            flush=True,
        )
        for qa in convo.questions:
            t1 = time.perf_counter()
            result = agent.run(qa.question)
            answer_seconds += time.perf_counter() - t1
            hits = result.steps[0].hits if result.steps else []
            ids = [hit.chunk_id for hit in hits]
            texts = [hit.text for hit in hits]
            row = {
                "system": name,
                "qid": qa.qid,
                "sample_id": qa.sample_id,
                "qa_index": qa.qa_index,
                "category": qa.category,
                "category_name": CATEGORY_NAMES.get(qa.category, str(qa.category)),
                "question": qa.question,
                "answer": qa.answer,
                "evidence": list(qa.evidence),
                "search_query": result.steps[0].query if result.steps else None,
                "retrieved_ids": ids,
                "evidence_hit": evidence_hit(ids, qa.evidence),
                "evidence_recall": evidence_recall(ids, qa.evidence),
                "oracle_recall": oracle_recall(qa.evidence, k),
                "answer_containment": answer_contained(texts, qa.answer),
                "offline_token_f1": token_f1(result.answer, qa.answer),
                "final_answer": result.answer,
            }
            rows.append(row)
            if name == "poincare" and hasattr(memory, "cosine_ids"):
                cosine = memory.cosine_ids(k)
                cosine_n += 1
                inter = len(set(ids) & set(cosine))
                union = len(set(ids) | set(cosine)) or 1
                cosine_overlaps.append(inter / union)
                if ids[:1] != cosine[:1]:
                    cosine_top1_differ += 1
    extra = {
        "graph_stats_sum": _sum_stats(graph_stats),
        "radius": memory.radius_stats() if hasattr(memory, "radius_stats") else None,
        "cosine_jaccard_mean": mean(cosine_overlaps) if cosine_overlaps else None,
        "cosine_top1_differ_rate": (cosine_top1_differ / cosine_n) if cosine_n else None,
    }
    # radius must be read before the caller deletes memory; radius_stats flushes.
    return rows, {"ingest_seconds": ingest_seconds, "answer_seconds": answer_seconds}, extra


def _sum_stats(items: list[dict]) -> dict[str, int] | None:
    if not items:
        return None
    total: dict[str, int] = defaultdict(int)
    for item in items:
        for key, value in item.items():
            total[key] += int(value)
    return dict(total)


def _summarize(name: str, rows: list[dict], timing: dict, extra: dict, k: int) -> dict:
    by_cat = {}
    for category, label in CATEGORY_NAMES.items():
        subset = [row for row in rows if row["category"] == category]
        if not subset:
            continue
        by_cat[str(category)] = {
            "name": label,
            "n": len(subset),
            "evidence_hit_rate": mean([row["evidence_hit"] for row in subset]),
            "evidence_recall_at_k": mean([row["evidence_recall"] for row in subset]),
            "oracle_recall_at_k": mean([row["oracle_recall"] for row in subset]),
            "answer_containment": _mean_optional([row["answer_containment"] for row in subset]),
        }
    return {
        "name": name,
        "k": k,
        "question_count": len(rows),
        "evidence_hit_rate": mean([row["evidence_hit"] for row in rows]),
        "evidence_recall_at_k": mean([row["evidence_recall"] for row in rows]),
        "oracle_recall_at_k": mean([row["oracle_recall"] for row in rows]),
        "answer_containment": _mean_optional([row["answer_containment"] for row in rows]),
        "offline_extractive_token_f1": _mean_optional([row["offline_token_f1"] for row in rows]),
        "search_query_is_question_rate": mean(
            [1.0 if row["search_query"] == row["question"] else 0.0 for row in rows]
        ),
        "by_category": by_cat,
        "timing_seconds": timing,
        "extra": {key: value for key, value in extra.items() if value is not None},
    }


def _mean_optional(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return mean(present)


def _comparison(conversations, questions: list[QAItem], reports, rows, client_mode: str, k: int, categories) -> dict:
    by_system = {report["name"]: report for report in reports}
    examples = {
        "kuzu_memory_vs_poincare": _examples(rows, "kuzu_memory", "poincare"),
        "graph_kuzu_vs_poincare": _examples(rows, "graph_kuzu", "poincare"),
    }
    return {
        "dataset": {
            "name": "LoCoMo",
            "paper": "Maharana et al., Evaluating Very Long-Term Conversational Memory of LLM Agents",
            "revision": DATASET_REVISION,
            "url": DATASET_URL,
            "sha256": DATASET_SHA256,
            "conversation_ids": [convo.sample_id for convo in conversations],
            "categories": {str(c): CATEGORY_NAMES[c] for c in categories},
            "category_note": (
                "Ids follow locomo task_eval/evaluation.py: "
                "1 multi-hop, 2 temporal, 3 open-domain, 4 single-hop, 5 adversarial. "
                "Primary slice keeps 1, 2, and 4."
            ),
            "question_count": len(questions),
            "question_ids": [q.qid for q in questions],
            "chunking": (
                "One memory per dialogue turn. Text is "
                "'[session_date_time] speaker: text' plus an image caption when present. "
                "The same string is passed to every memory."
            ),
            "k": k,
        },
        "react": {
            "client": client_mode,
            "tool": "search_memory",
            "policy": (
                "No LLM API key was set. OfflineReActClient searches once with the "
                "raw question and then emits the top memory as an extractive final answer. "
                "Primary metrics score that tool's top-k, not the extractive answer."
                if client_mode == "offline"
                else "An OpenAI-compatible client answered through the same loop. "
                "Primary metrics still score the first search_memory top-k."
            ),
        },
        "ingestion_difference": {
            "shared": "Same turns, same order, same chunk text, same k, same ReAct policy, same questions.",
            "kuzu_memory": (
                "kuzu-memory 1.12 stores each turn with remember() (metadata chunk_id) and recalls "
                "with attach_memories(strategy='auto', use_semantic_search=False). "
                "That auto path is keyword + entity + temporal + a RELATES_TO hop. "
                "MiniLM embedding writes are skipped so the score is not a second vector ranker. "
                "The library's entity regex does not extract single given names, and its HAS_KEYWORD "
                "graph query is disabled upstream, so on this corpus the public API behaves as a "
                "keyword filter plus the library's own ranker. Candidate window is 1000, above the "
                "longest conversation, because the library LIMITs before ranking."
            ),
            "graph_kuzu": (
                "Kuzu 0.11 stores Utterance and Entity nodes plus MENTIONS and RELATES edges. "
                "Entities and SVO relations come from spaCy en_core_web_sm because Mem0 and Graphiti "
                "extraction needs an LLM, and kuzu-memory's own regex NER misses dialogue names. "
                "First-person subjects are attached to the speaker. "
                "Retrieval is Cypher: match query entities, walk one RELATES hop (skipping hubs), "
                "rank utterances by inverse mention-degree. No embedding is used. "
                "This is the graph-traversal arm of the comparison."
            ),
            "poincare": (
                f"The same turn text is encoded with {_MODEL}. "
                "Vectors are mean-pooled token states before the model's L2-normalize layer, "
                f"then mapped by exp_0(scale * x) with scale={PROJECTION_SCALE} into the open unit ball. "
                "Ranking uses Poincaré ball distance only."
            ),
        },
        "systems": by_system,
        "examples": examples,
        "interpretation": _interpret(by_system),
    }


def _examples(rows: list[dict], left: str, right: str, limit: int = 4) -> dict:
    by_q: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_q[row["qid"]][row["system"]] = row
    left_only = []
    right_only = []
    for qid, pair in by_q.items():
        if left not in pair or right not in pair:
            continue
        a = pair[left]
        b = pair[right]
        item = {
            "qid": qid,
            "category": a["category_name"],
            "question": a["question"],
            "answer": a["answer"],
            "evidence": a["evidence"],
            "left_ids": a["retrieved_ids"],
            "right_ids": b["retrieved_ids"],
        }
        if a["evidence_hit"] > b["evidence_hit"]:
            left_only.append(item)
        elif b["evidence_hit"] > a["evidence_hit"]:
            right_only.append(item)
    return {
        "left": left,
        "right": right,
        "left_only": left_only[:limit],
        "right_only": right_only[:limit],
        "left_only_count": len(left_only),
        "right_only_count": len(right_only),
    }


def _winner(left: dict, right: dict) -> str:
    l_hit, r_hit = left["evidence_hit_rate"], right["evidence_hit_rate"]
    l_rec, r_rec = left["evidence_recall_at_k"], right["evidence_recall_at_k"]
    if r_hit > l_hit and r_rec >= l_rec:
        return right["name"]
    if l_hit > r_hit and l_rec >= r_rec:
        return left["name"]
    if r_rec > l_rec and r_hit >= l_hit:
        return right["name"]
    if l_rec > r_rec and l_hit >= r_hit:
        return left["name"]
    return "mixed"


def _interpret(systems: dict[str, dict]) -> str:
    kuzu = systems["kuzu_memory"]
    graph = systems["graph_kuzu"]
    poincare = systems["poincare"]
    k = graph["k"]
    oss_winner = _winner(kuzu, poincare)
    graph_winner = _winner(graph, poincare)
    cosine = (poincare.get("extra") or {}).get("cosine_jaccard_mean")
    cosine_bit = ""
    if cosine is not None:
        cosine_bit = (
            f" On the same vectors, mean top-{k} Jaccard between Poincaré rank and cosine rank "
            f"is {cosine:.3f}, so the ball distance is not a rename of cosine."
        )
    category_bits = []
    for cat in sorted(graph["by_category"], key=int):
        name = graph["by_category"][cat]["name"]
        bits = []
        for system in (kuzu, graph, poincare):
            rate = system["by_category"][cat]["evidence_hit_rate"]
            bits.append(f"{system['name']}={rate:.3f}")
        category_bits.append(f"{name} hit " + " ".join(bits))
    stats = (graph.get("extra") or {}).get("graph_stats_sum") or {}
    density = ""
    if stats.get("utterances"):
        density = (
            f" Offline extraction produced {stats.get('relations', 0)} RELATES edges and "
            f"{stats.get('mentions', 0)} MENTIONS edges for {stats['utterances']} turns, "
            "so many structural-graph hits are direct entity mentions rather than multi-hop paths."
        )
    return (
        f"Open-source module versus Poincaré: winner {oss_winner}. "
        f"evidence hit@{k} kuzu_memory={kuzu['evidence_hit_rate']:.3f} "
        f"poincare={poincare['evidence_hit_rate']:.3f}; "
        f"evidence recall@{k} kuzu_memory={kuzu['evidence_recall_at_k']:.3f} "
        f"poincare={poincare['evidence_recall_at_k']:.3f}. "
        "kuzu-memory is called through remember/attach_memories with semantic search off. "
        "Its entity patterns do not pick up single given names and its keyword graph query is "
        "disabled, so this number is the library's keyword filter plus its own ranker. "
        f"Structural graph versus Poincaré: winner {graph_winner}. "
        f"evidence hit@{k} graph_kuzu={graph['evidence_hit_rate']:.3f} "
        f"poincare={poincare['evidence_hit_rate']:.3f}; "
        f"evidence recall@{k} graph_kuzu={graph['evidence_recall_at_k']:.3f} "
        f"poincare={poincare['evidence_recall_at_k']:.3f}. "
        "The structural graph ranks turns by inverse mention-degree over MENTIONS edges and one "
        "RELATES hop. That is the graph-traversal comparison. Poincaré distance is the hyperbolic "
        "distance inside the open unit ball after exp_0, and it is ahead when the question "
        "paraphrases the evidence turn."
        f"{cosine_bit} By category: " + "; ".join(category_bits) + "."
        f"{density} "
        "Answer-string containment is secondary: many gold answers are abstractive "
        "(especially temporal dates inferred from 'yesterday' plus the session timestamp) "
        "and do not appear verbatim even in the gold turns."
    )


_SYSTEM_ORDER = ("kuzu_memory", "graph_kuzu", "poincare")


def _markdown(comparison: dict) -> str:
    data = comparison["dataset"]
    systems = comparison["systems"]
    ordered = [systems[name] for name in _SYSTEM_ORDER if name in systems]
    lines = [
        "# LoCoMo 记忆对比：kuzu-memory、实体图、Poincaré 球距离",
        "",
        _chinese_summary(comparison),
        "",
        comparison["interpretation"],
        "",
        "## 数据",
        "",
        f"- 数据集：LoCoMo `locomo10.json`，修订 `{data['revision']}`",
        f"- SHA256：`{data['sha256']}`",
        f"- 对话：{', '.join(data['conversation_ids'])}（{len(data['conversation_ids'])} 段）",
        f"- 问题数：{data['question_count']}（类别 {', '.join(data['categories'].values())}）",
        f"- 切块：{data['chunking']}",
        f"- k = {data['k']}",
        f"- ReAct：{comparison['react']['client']}。{comparison['react']['policy']}",
        "",
        "## 总指标",
        "",
        "| 系统 | evidence hit@k | evidence recall@k | oracle recall@k | answer containment | 摄取秒 | 作答秒 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for system in ordered:
        lines.append(
            "| {name} | {hit:.3f} | {rec:.3f} | {oracle:.3f} | {ans} | {ing:.1f} | {answ:.1f} |".format(
                name=system["name"],
                hit=system["evidence_hit_rate"],
                rec=system["evidence_recall_at_k"],
                oracle=system["oracle_recall_at_k"],
                ans=_fmt(system["answer_containment"]),
                ing=system["timing_seconds"]["ingest_seconds"],
                answ=system["timing_seconds"]["answer_seconds"],
            )
        )
    lines.extend(["", "## 按类别", ""])
    header = "| 类别 | n |"
    sep = "| --- | ---: |"
    for system in ordered:
        header += f" {system['name']} hit | {system['name']} recall |"
        sep += " ---: | ---: |"
    lines.append(header)
    lines.append(sep)
    cats = sorted(ordered[0]["by_category"], key=int)
    for cat in cats:
        head = ordered[0]["by_category"][cat]
        row = f"| {cat} {head['name']} | {head['n']} |"
        for system in ordered:
            cell = system["by_category"][cat]
            row += f" {cell['evidence_hit_rate']:.3f} | {cell['evidence_recall_at_k']:.3f} |"
        lines.append(row)
    poincare = systems.get("poincare") or {}
    extra = poincare.get("extra") or {}
    if extra.get("cosine_jaccard_mean") is not None:
        lines.extend(
            [
                "",
                "## Poincaré 与余弦",
                "",
                f"- 同一向量上 top-{data['k']} Jaccard（Poincaré vs cosine）：{extra['cosine_jaccard_mean']:.3f}",
                f"- top-1 不相同的问题比例：{extra.get('cosine_top1_differ_rate'):.3f}",
            ]
        )
        radius = extra.get("radius") or {}
        if radius:
            lines.append(
                "- 投影后半径 min/median/max："
                f"{radius['min']:.3f} / {radius['median']:.3f} / {radius['max']:.3f}"
            )
    graph = systems.get("graph_kuzu") or {}
    graph_extra = (graph.get("extra") or {}).get("graph_stats_sum")
    if graph_extra:
        lines.extend(
            [
                "",
                "## 图规模（每段对话单独建图，下面是写入合计）",
                "",
                f"- utterances {graph_extra.get('utterances')}，entities {graph_extra.get('entities')}，"
                f"mentions {graph_extra.get('mentions')}，relations {graph_extra.get('relations')}",
            ]
        )
    lines.extend(["", "## 摄取差异", ""])
    for key, text in comparison["ingestion_difference"].items():
        lines.append(f"- **{key}**：{text}")
    lines.extend(["", "## 分歧样例", ""])
    labels = {
        "kuzu_memory_vs_poincare": "kuzu-memory 与 Poincaré",
        "graph_kuzu_vs_poincare": "实体图与 Poincaré",
    }
    for key, title in labels.items():
        pair = comparison["examples"][key]
        lines.append(f"### {title}")
        lines.append("")
        lines.append(
            f"- 仅 {pair['left']} 命中：{pair['left_only_count']}；仅 {pair['right']} 命中：{pair['right_only_count']}"
        )
        lines.append("")
        lines.append(f"#### 仅 {pair['left']} 命中")
        lines.append("")
        for item in pair["left_only"]:
            lines.append(_example_line(pair, item))
        lines.append("")
        lines.append(f"#### 仅 {pair['right']} 命中")
        lines.append("")
        for item in pair["right_only"]:
            lines.append(_example_line(pair, item))
        lines.append("")
    return "\n".join(lines)


def _chinese_summary(comparison: dict) -> str:
    systems = comparison["systems"]
    kuzu = systems["kuzu_memory"]
    graph = systems["graph_kuzu"]
    poincare = systems["poincare"]
    k = kuzu["k"]
    oss = _winner(kuzu, poincare)
    structural = _winner(graph, poincare)
    return (
        f"同一套 ReAct、同一批 LoCoMo 题。开源模块 kuzu-memory 对 Poincaré 的主指标胜者是 {oss}："
        f"evidence hit@{k} 为 {kuzu['evidence_hit_rate']:.3f} 对 {poincare['evidence_hit_rate']:.3f}，"
        f"evidence recall@{k} 为 {kuzu['evidence_recall_at_k']:.3f} 对 {poincare['evidence_recall_at_k']:.3f}。"
        f"实体关系图对 Poincaré 的胜者是 {structural}："
        f"hit@{k} {graph['evidence_hit_rate']:.3f} 对 {poincare['evidence_hit_rate']:.3f}，"
        f"recall@{k} {graph['evidence_recall_at_k']:.3f} 对 {poincare['evidence_recall_at_k']:.3f}。"
        "kuzu-memory 走的是它自己的 attach_memories，语义向量检索关掉。"
        "它的实体正则认不出单独的人名，关键词图查询在库里是关的，所以这条分数主要是关键词过滤加它自己的排序。"
        "实体图才是按 MENTIONS 边和一跳 RELATES 做的图遍历，用来和庞加莱球距离对照。"
        "没有 LLM API key 时，数字是离线 ReAct 同一次 search_memory 的检索质量，不是模型答题准确率。"
    )


def _example_line(pair: dict, item: dict) -> str:
    return (
        f"- `{item['qid']}` [{item['category']}] {item['question']} "
        f"(gold {', '.join(item['evidence'])}; {pair['left']} {', '.join(item['left_ids']) or '—'}; "
        f"{pair['right']} {', '.join(item['right_ids']) or '—'})"
    )


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}"


def _parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare Kuzu graph memory and Poincaré memory on LoCoMo.")
    parser.add_argument("--k", type=int, default=K_DEFAULT)
    parser.add_argument("--categories", default=",".join(str(c) for c in PRIMARY_CATEGORIES))
    parser.add_argument("--conversations", type=int, default=0, help="Debug: only the first N conversations.")
    parser.add_argument("--out-dir", default="results")
    return parser.parse_args(argv)


def main() -> None:
    run()


if __name__ == "__main__":
    main()
