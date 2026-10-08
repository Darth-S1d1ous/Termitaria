"""Open-source graph memory: kuzu-memory's public remember / attach_memories API.

kuzu-memory stores each turn as a Memory node in an embedded Kuzu graph and
recalls with its auto strategy (keyword, entity, temporal, and a RELATES_TO
hop). Semantic search stays off, so MiniLM vectors are not part of the score.

On this dialogue data the library's own graph indexes barely engage: its
entity regex looks for product and full-name patterns, not a single given
name, and the shipped keyword path scans memory text instead of the
HAS_KEYWORD edges (that graph query is disabled inside the library). The
adapter still calls the public recall API unchanged. Background enrichment
and embedding writes are turned off so bulk ingest does not fight Kuzu's
single writer for indexes this recall path does not read.

The library applies LIMIT inside each strategy before it ranks, and that
limit follows recency. The candidate window is larger than one LoCoMo
conversation (the longest is under 700 turns) so the library's ranker sees
every match, then this adapter keeps the first k of that order.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from memcompare.memory import Memory, MemoryHit

logger = logging.getLogger(__name__)

# Longer than the longest LoCoMo conversation, so the pre-rank LIMIT is not a
# recency cutoff.
_CANDIDATE_WINDOW = 1000

_EMBED_DISABLED = False


def _disable_embedding_writes() -> None:
    """Skip the library's MiniLM write path. Recall does not read those vectors."""

    global _EMBED_DISABLED
    if _EMBED_DISABLED:
        return
    from kuzu_memory.recall.coordinator import _SemanticScorer

    def _no_embed(self: Any, text: str) -> None:
        return None

    _SemanticScorer.embed = _no_embed  # type: ignore[method-assign]
    _EMBED_DISABLED = True


class KuzuOpenMemory(Memory):
    name = "kuzu_memory"

    def __init__(self, candidate_window: int = _CANDIDATE_WINDOW) -> None:
        self.candidate_window = candidate_window
        self._km: Any = None
        self._tmp: tempfile.TemporaryDirectory[str] | None = None
        self._stored = 0
        self._open()

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        meta = metadata or {}
        chunk_id = str(meta.get("chunk_id") or f"c{self._stored}")
        memory_id = self._km.remember(
            text,
            source="locomo",
            metadata={"chunk_id": chunk_id},
            session_id="locomo",
        )
        if memory_id:
            self._stored += 1

    def retrieve(self, query: str, k: int = 5) -> list[MemoryHit]:
        if k <= 0 or self._stored == 0:
            return []
        window = max(k, self.candidate_window)
        context = self._km.attach_memories(
            query,
            max_memories=window,
            strategy="auto",
            use_semantic_search=False,
        )
        hits: list[MemoryHit] = []
        seen: set[str] = set()
        for index, memory in enumerate(context.memories):
            chunk_id = _chunk_id(memory)
            if not chunk_id or chunk_id in seen:
                continue
            seen.add(chunk_id)
            hits.append(
                MemoryHit(
                    chunk_id=chunk_id,
                    text=memory.content,
                    score=float(window - index),
                    detail={"strategy": context.strategy_used, "memory_id": memory.id},
                )
            )
            if len(hits) >= k:
                break
        return hits

    def clear(self) -> None:
        self._close()
        self._stored = 0
        self._open()

    def _flush(self) -> None:
        return None

    @property
    def stats(self) -> dict[str, int]:
        return {"memories": self._stored}

    def _open(self) -> None:
        _disable_embedding_writes()
        from kuzu_memory import KuzuMemory
        from kuzu_memory.core.config import KuzuMemoryConfig
        from kuzu_memory.core.dependencies import reset_container

        reset_container()
        self._tmp = tempfile.TemporaryDirectory(prefix="kuzu-open-memory-")
        db_path = Path(self._tmp.name) / "memories.db"
        config = KuzuMemoryConfig.default()
        config.memory.auto_tag_git_user = False
        config.recall.tfidf_boost_weight = 0.0
        config.performance.log_slow_operations = False
        self._km = KuzuMemory(
            db_path=db_path,
            config=config,
            enable_git_sync=False,
            auto_sync=False,
        )
        # Enrichment is a background writer. The keyword recall path used here
        # does not read those edges, and Kuzu allows one writer.
        self._km._maybe_enrich = lambda writes=1: None

    def _close(self) -> None:
        km = self._km
        self._km = None
        if km is not None:
            try:
                km.close()
            except Exception:
                logger.debug("kuzu-memory close failed", exc_info=True)
        tmp = self._tmp
        self._tmp = None
        if tmp is not None:
            tmp.cleanup()
            shutil.rmtree(tmp.name, ignore_errors=True)


def _chunk_id(memory: Any) -> str:
    metadata = getattr(memory, "metadata", None) or {}
    if isinstance(metadata, dict):
        value = metadata.get("chunk_id")
        if value:
            return str(value)
    return ""
