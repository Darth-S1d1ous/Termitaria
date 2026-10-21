"""Mem0 事实记忆：LLM 抽事实，向量放在 Qdrant，按 agent_id 隔离。

库的 add / search 是同步的，这里用 to_thread 包一层。
安装：uv sync --extra memory
"""
from __future__ import annotations

import asyncio
import time

from termitaria.memory.v1 import memory_pb2

from .config import MemoryConfig
from .items import assemble_response, mem0_items

# all-MiniLM-L6-v2 的向量长度。Qdrant collection 必须和嵌入模型一致。
_EMBED_DIMS = 384


class Mem0Backend:
    name = "mem0"
    degraded = False

    def __init__(self, cfg: MemoryConfig) -> None:
        from mem0 import Memory

        self._memory = Memory.from_config(
            {
                "vector_store": {
                    "provider": "qdrant",
                    "config": {
                        "collection_name": cfg.qdrant_collection,
                        "host": cfg.qdrant_host,
                        "port": cfg.qdrant_port,
                        "embedding_model_dims": _EMBED_DIMS,
                    },
                },
                "llm": {
                    "provider": "openai",
                    "config": {
                        "model": cfg.llm_model,
                        "temperature": 0.1,
                        "api_key": cfg.llm_api_key,
                        "openai_base_url": cfg.llm_base_url,
                    },
                },
                "embedder": {
                    "provider": "huggingface",
                    "config": {
                        "model": cfg.embed_model,
                        "embedding_dims": _EMBED_DIMS,
                    },
                },
            }
        )
        self._lock = asyncio.Lock()

    async def recall(self, req: memory_pb2.RecallRequest) -> memory_pb2.RecallResponse:
        started = time.time()
        limit = req.top_k or 5
        async with self._lock:
            raw = await asyncio.to_thread(
                self._memory.search,
                req.query_text,
                agent_id=req.agent_id,
                limit=limit,
            )
        hits = _hits(raw)
        return assemble_response(req, mem0_items(req.agent_id, hits), started=started)

    async def write_episode(self, req: memory_pb2.WriteEpisodeRequest) -> str:
        episode = req.episode
        text = episode.summary.strip()
        if not text:
            return ""
        async with self._lock:
            raw = await asyncio.to_thread(
                self._memory.add,
                [{"role": "user", "content": text}],
                agent_id=episode.agent_id,
                run_id=episode.session_id,
                metadata={"occurred_at_ms": episode.occurred_at_ms, "run_id": episode.session_id},
            )
        return _first_id(raw)

    async def write_document(self, req: memory_pb2.WriteDocumentRequest) -> str:
        doc = req.document
        text = "\n".join(part for part in (doc.title, doc.uri, doc.content_ref) if part)
        if not text:
            return doc.doc_id
        who = doc.uploader_id or "documents"
        async with self._lock:
            await asyncio.to_thread(
                self._memory.add,
                [{"role": "user", "content": text}],
                agent_id=who,
                metadata={"doc_id": doc.doc_id},
            )
        return doc.doc_id


def _hits(raw) -> list[dict]:
    if isinstance(raw, dict):
        results = raw.get("results", raw.get("memories", []))
        if isinstance(results, dict):
            results = results.get("results", [])
        return [hit for hit in (results or []) if isinstance(hit, dict)]
    if isinstance(raw, list):
        return [hit for hit in raw if isinstance(hit, dict)]
    return []


def _first_id(raw) -> str:
    for hit in _hits(raw):
        if hit.get("id"):
            return str(hit["id"])
    return ""
