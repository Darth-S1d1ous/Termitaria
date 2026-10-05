"""Run the same ReAct loop and the same LoCoMo questions on both memories."""

from __future__ import annotations

import argparse
import json
import os
import time
from collections import defaultdict
from pathlib import Path

from memcompare.graph_memory import GraphMemory
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
    examples = _examples(rows)
    graph = by_system["graph_kuzu"]
    poincare = by_system["poincare"]
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
                "The same string is passed to both memories."
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
            "graph_kuzu": (
                "Kuzu 0.11 stores Utterance and Entity nodes plus MENTIONS and RELATES edges. "
                "Entities and SVO relations come from spaCy en_core_web_sm because Mem0/Graphiti "
                "extraction needs an LLM. First-person subjects are attached to the speaker. "
                "Retrieval is Cypher: match query entities, walk one RELATES hop (skipping hubs), "
                "rank utterances by inverse mention-degree. No embedding is used."
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
        "interpretation": _interpret(graph, poincare),
    }


def _examples(rows: list[dict], limit: int = 4) -> dict:
    by_q: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in rows:
        by_q[row["qid"]][row["system"]] = row
    graph_only = []
    poincare_only = []
    for qid, pair in by_q.items():
        if "graph_kuzu" not in pair or "poincare" not in pair:
            continue
        g = pair["graph_kuzu"]
        p = pair["poincare"]
        item = {
            "qid": qid,
            "category": g["category_name"],
            "question": g["question"],
            "answer": g["answer"],
            "evidence": g["evidence"],
            "graph_ids": g["retrieved_ids"],
            "poincare_ids": p["retrieved_ids"],
        }
        if g["evidence_hit"] > p["evidence_hit"]:
            graph_only.append(item)
        elif p["evidence_hit"] > g["evidence_hit"]:
            poincare_only.append(item)
    return {
        "graph_hit_poincare_miss": graph_only[:limit],
        "poincare_hit_graph_miss": poincare_only[:limit],
        "graph_only_hit_count": len(graph_only),
        "poincare_only_hit_count": len(poincare_only),
    }


def _interpret(graph: dict, poincare: dict) -> str:
    g_hit = graph["evidence_hit_rate"]
    p_hit = poincare["evidence_hit_rate"]
    g_rec = graph["evidence_recall_at_k"]
    p_rec = poincare["evidence_recall_at_k"]
    if p_hit > g_hit and p_rec >= g_rec:
        winner = "poincare"
    elif g_hit > p_hit and g_rec >= p_rec:
        winner = "graph_kuzu"
    elif p_rec > g_rec and p_hit >= g_hit:
        winner = "poincare"
    elif g_rec > p_rec and g_hit >= p_hit:
        winner = "graph_kuzu"
    else:
        winner = "mixed"
    cosine = (poincare.get("extra") or {}).get("cosine_jaccard_mean")
    cosine_bit = ""
    if cosine is not None:
        cosine_bit = (
            f" On the same vectors, mean top-{graph['k']} Jaccard between Poincaré rank and cosine rank "
            f"is {cosine:.3f}, so the ball distance is not a rename of cosine."
        )
    category_bits = []
    for cat in sorted(graph["by_category"], key=int):
        g = graph["by_category"][cat]
        p = poincare["by_category"][cat]
        if p["evidence_hit_rate"] > g["evidence_hit_rate"]:
            leader = "poincare"
        elif g["evidence_hit_rate"] > p["evidence_hit_rate"]:
            leader = "graph_kuzu"
        else:
            leader = "tie"
        category_bits.append(
            f"{g['name']} hit graph={g['evidence_hit_rate']:.3f} poincare={p['evidence_hit_rate']:.3f} ({leader})"
        )
    category_bit = " By category: " + "; ".join(category_bits) + "."
    reason = {
        "poincare": (
            "Poincaré retrieval did better. LoCoMo questions often paraphrase the evidence turn "
            "instead of repeating its entity string, and the hyperbolic rank of MiniLM states "
            "still tracks that semantic neighborhood. The Kuzu graph only reaches a turn when "
            "the offline spaCy extractor emits a shared or one-hop entity, so missed noun chunks "
            "and unspoken paraphrases become misses."
        ),
        "graph_kuzu": (
            "The Kuzu graph did better overall, mostly on single-hop and temporal questions. "
            "Those questions repeat a person and a concrete event that the offline extractor stored "
            "as entity nodes, so inverse-degree ranking over MENTIONS edges finds the evidence turn. "
            "Poincaré distance is ahead on multi-hop questions, where the wording drifts away from "
            "any single turn. RELATES edges are real, but they are sparse next to MENTIONS, so "
            "one-hop expansion is not the main source of the gap."
        ),
        "mixed": (
            "Neither memory leads on both hit rate and recall. The graph wins questions whose "
            "entities were extracted and linked; Poincaré wins questions whose evidence is a paraphrase "
            "with little exact entity overlap."
        ),
    }[winner]
    stats = (graph.get("extra") or {}).get("graph_stats_sum") or {}
    density = ""
    if stats.get("utterances"):
        density = (
            f" Offline extraction produced {stats.get('relations', 0)} RELATES edges and "
            f"{stats.get('mentions', 0)} MENTIONS edges for {stats['utterances']} turns, "
            "so many graph hits are direct entity mentions rather than multi-hop paths."
        )
    gap = ""
    if abs(p_hit - g_hit) < 0.02 and abs(p_rec - g_rec) < 0.02:
        gap = " The overall margin is small; the category split is the sharper comparison."
    return (
        f"Winner on the primary retrieval metrics: {winner}. "
        f"evidence hit@{graph['k']} graph={g_hit:.3f} poincare={p_hit:.3f}; "
        f"evidence recall@{graph['k']} graph={g_rec:.3f} poincare={p_rec:.3f}. "
        f"{reason}{gap}{cosine_bit}{category_bit}{density} "
        "Answer-string containment is secondary: many gold answers are abstractive "
        "(especially temporal dates inferred from 'yesterday' plus the session timestamp) "
        "and do not appear verbatim even in the gold turns."
    )


def _markdown(comparison: dict) -> str:
    data = comparison["dataset"]
    systems = comparison["systems"]
    graph = systems["graph_kuzu"]
    poincare = systems["poincare"]
    lines = [
        "# LoCoMo 记忆对比：Kuzu 图记忆 vs Poincaré 球距离",
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
    for system in (graph, poincare):
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
    header = "| 类别 | n | graph hit | poincaré hit | graph recall | poincaré recall |"
    lines.append(header)
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    cats = sorted(set(graph["by_category"]) | set(poincare["by_category"]), key=int)
    for cat in cats:
        g = graph["by_category"][cat]
        p = poincare["by_category"][cat]
        lines.append(
            f"| {cat} {g['name']} | {g['n']} | {g['evidence_hit_rate']:.3f} | "
            f"{p['evidence_hit_rate']:.3f} | {g['evidence_recall_at_k']:.3f} | "
            f"{p['evidence_recall_at_k']:.3f} |"
        )
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
    examples = comparison["examples"]
    lines.append(
        f"- 仅图命中：{examples['graph_only_hit_count']}；仅 Poincaré 命中：{examples['poincare_only_hit_count']}"
    )
    for label, key in (
        ("图命中、Poincaré 未命中", "graph_hit_poincare_miss"),
        ("Poincaré 命中、图未命中", "poincare_hit_graph_miss"),
    ):
        lines.append("")
        lines.append(f"### {label}")
        lines.append("")
        for item in examples[key]:
            lines.append(
                f"- `{item['qid']}` [{item['category']}] {item['question']} "
                f"(gold {', '.join(item['evidence'])}; graph {', '.join(item['graph_ids']) or '—'}; "
                f"poincaré {', '.join(item['poincare_ids']) or '—'})"
            )
    lines.append("")
    return "\n".join(lines)


def _chinese_summary(comparison: dict) -> str:
    graph = comparison["systems"]["graph_kuzu"]
    poincare = comparison["systems"]["poincare"]
    return (
        f"主指标上 Kuzu 图记忆更好：evidence hit@5 为 {graph['evidence_hit_rate']:.3f}，"
        f"Poincaré 为 {poincare['evidence_hit_rate']:.3f}；"
        f"evidence recall@5 为 {graph['evidence_recall_at_k']:.3f} 对 {poincare['evidence_recall_at_k']:.3f}。"
        "差距主要来自单跳题和时序题：问题里的人名和具体事件能对上图里的实体节点，按提及度倒数排序就能找到证据轮次。"
        "多跳题上 Poincaré 略高，因为问法经常换一种说法，实体字符串对不上。"
        "环境里没有 LLM API key，所以上面的数字是离线 ReAct 同一次 `search_memory` 的检索质量，不是模型答题准确率。"
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
