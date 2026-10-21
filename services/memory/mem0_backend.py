"""Mem0 事实记忆：用户原句直接入库，向量放在 Qdrant，按 agent_id 隔离。

库的 add / search 是同步的，这里用 to_thread 包一层。
add 关掉 infer，避免模型把原句抽空。
安装：uv sync --extra memory
"""
from __future__ import annotations

import asyncio
import time

from termitaria.memory.v1 import memory_pb2

from .config import MemoryConfig
from .items import assemble_response, mem0_items
from .timing import time_warm_queries

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

    async def warmup(self) -> list[int]:
        async def embed(text: str) -> None:
            await self._encode(text)

        async def once() -> int:
            resp = await self.recall(
                memory_pb2.RecallRequest(agent_id="_warmup", query_text="hello", top_k=1)
            )
            return resp.meta.latency_ms

        return await time_warm_queries(embed, once)

    async def _encode(self, text: str) -> None:
        def run() -> None:
            model = getattr(self._memory, "embedding_model", None)
            embed = getattr(model, "embed", None) if model is not None else None
            if embed is not None:
                embed(text)
                return
            self._memory.search(text, agent_id="_warmup", limit=1)

        async with self._lock:
            await asyncio.to_thread(run)

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
        messages, kwargs = add_messages(
            text,
            agent_id=episode.agent_id,
            run_id=episode.session_id,
            metadata={"occurred_at_ms": episode.occurred_at_ms, "run_id": episode.session_id},
        )
        async with self._lock:
            raw = await asyncio.to_thread(self._memory.add, messages, **kwargs)
        return _first_id(raw)

    async def write_document(self, req: memory_pb2.WriteDocumentRequest) -> str:
        doc = req.document
        text = "\n".join(part for part in (doc.title, doc.uri, doc.content_ref) if part)
        if not text:
            return doc.doc_id
        who = doc.uploader_id or "documents"
        messages, kwargs = add_messages(
            text,
            agent_id=who,
            metadata={"doc_id": doc.doc_id},
        )
        async with self._lock:
            await asyncio.to_thread(self._memory.add, messages, **kwargs)
        return doc.doc_id


def add_messages(
    text: str,
    *,
    agent_id: str,
    run_id: str = "",
    metadata: dict | None = None,
) -> tuple[list[dict], dict]:
    kwargs = {
        "agent_id": agent_id,
        "metadata": metadata or {},
        "infer": False,
    }
    if run_id:
        kwargs["run_id"] = run_id
    return [{"role": "user", "content": text}], kwargs


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
