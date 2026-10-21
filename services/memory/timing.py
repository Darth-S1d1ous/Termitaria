"""启动时先跑一次嵌入，再用几次热查询决定 dispatcher 该等多久。

冷启动那一次不拿来定等待时间。等待取热查询里较慢的一次，再留一点余量。
真实检索曾经大约 700ms 才返回，所以等待不低于 1200ms。
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

WARM_QUERIES = 3
_WAIT_FLOOR_MS = 1200
_WAIT_FILE = Path("data/recall-wait-ms")


def recommend_recall_wait_ms(samples: list[int]) -> int:
    positive = [n for n in samples if n > 0]
    if not positive:
        return 0
    slowest = max(positive)
    padded = slowest + max(slowest // 2, 100)
    return max(padded, _WAIT_FLOOR_MS)


async def time_warm_queries(embed, recall) -> list[int]:
    await embed("warmup")
    samples = []
    for _ in range(WARM_QUERIES):
        samples.append(int(await recall()))
    return samples


async def publish_recall_wait(backend, path: Path = _WAIT_FILE) -> int:
    warmup = getattr(backend, "warmup", None)
    if warmup is None:
        return 0
    samples = list(await warmup())
    wait = recommend_recall_wait_ms(samples)
    log.info(
        "warm recall backend=%s latency_ms=%s wait_ms=%s",
        getattr(backend, "name", ""),
        samples,
        wait,
    )
    if wait <= 0:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{wait}\n")
    return wait
