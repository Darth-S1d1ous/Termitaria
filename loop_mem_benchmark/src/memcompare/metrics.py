"""Retrieval metrics that do not need a judge model."""

from __future__ import annotations

from collections import Counter

from memcompare.textnorm import normalize


def evidence_hit(retrieved_ids: list[str], gold_ids: list[str] | tuple[str, ...]) -> float:
    return 1.0 if set(retrieved_ids) & set(gold_ids) else 0.0


def evidence_recall(retrieved_ids: list[str], gold_ids: list[str] | tuple[str, ...]) -> float:
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return len(set(retrieved_ids) & gold) / len(gold)


def oracle_recall(gold_ids: list[str] | tuple[str, ...], k: int) -> float:
    gold = set(gold_ids)
    if not gold:
        return 0.0
    return min(k, len(gold)) / len(gold)


def answer_contained(retrieved_texts: list[str], answer: str | None) -> float | None:
    if not answer:
        return None
    needle = normalize(answer)
    if not needle:
        return None
    blob = normalize(" ".join(retrieved_texts))
    return 1.0 if needle in blob else 0.0


def token_f1(prediction: str, answer: str | None) -> float | None:
    if not answer:
        return None
    pred_tokens = normalize(prediction).split()
    gold_tokens = normalize(answer).split()
    if not pred_tokens or not gold_tokens:
        return 0.0
    overlap = Counter(pred_tokens) & Counter(gold_tokens)
    common = sum(overlap.values())
    if common == 0:
        return 0.0
    precision = common / len(pred_tokens)
    recall = common / len(gold_tokens)
    return 2 * precision * recall / (precision + recall)


def mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))

