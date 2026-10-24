"""Graphiti 时间知识图：episode 抽实体和带有效期的事实，图存在 FalkorDB。

每个 agent 一个 group_id。节点摘要映射成 PRINCIPLE，边事实映射成 EPISODE。
安装：uv sync --extra memory
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from datetime import datetime, timezone

from termitaria.memory.v1 import memory_pb2

from .config import MemoryConfig
from .graph_view import agent_ok, build_graph_view, empty_graph_view
from .items import assemble_response, graphiti_items
from .timing import time_warm_queries

log = logging.getLogger(__name__)


class GraphitiBackend:
    name = "graphiti"
    degraded = False

    def __init__(self, cfg: MemoryConfig) -> None:
        # graphiti 在 import 时读 EMBEDDING_DIM，默认 1024。MiniLM 是 384。
        os.environ["EMBEDDING_DIM"] = "384"
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.llm_client.config import LLMConfig

        llm_config = LLMConfig(
            api_key=cfg.llm_api_key,
            model=cfg.llm_model,
            small_model=cfg.llm_model,
            base_url=cfg.llm_base_url,
        )
        driver = FalkorDriver(host=cfg.falkordb_host, port=cfg.falkordb_port)
        self._embedder = _local_embedder(cfg.embed_model)
        self._graphiti = Graphiti(
            graph_driver=driver,
            llm_client=_llm_client(llm_config),
            embedder=self._embedder,
            cross_encoder=_cross_encoder(llm_config),
        )
        self._cfg = cfg
        self._lock = asyncio.Lock()
        self._ready = False

    async def open(self) -> None:
        async with self._lock:
            if self._ready:
                return
            await self._graphiti.build_indices_and_constraints()
            self._ready = True

    async def warmup(self) -> list[int]:
        async def embed(text: str) -> None:
            await self._embedder.create(text)

        async def once() -> int:
            resp = await self.recall(
                memory_pb2.RecallRequest(agent_id="_warmup", query_text="hello", top_k=1)
            )
            return resp.meta.latency_ms

        return await time_warm_queries(embed, once)

    async def graph_view(self, agent_id: str) -> dict:
        if not agent_ok(agent_id):
            return empty_graph_view(self.name, agent_id)
        driver = self._graphiti.driver.clone(database=agent_id)
        try:
            async with self._lock:
                node_raw = await driver.execute_query(
                    "MATCH (n:Entity) RETURN n.uuid AS id, n.name AS name, n.summary AS summary LIMIT 80"
                )
                edge_raw = await driver.execute_query(
                    "MATCH (a:Entity)-[r]->(b:Entity) "
                    "RETURN r.uuid AS id, a.uuid AS source, b.uuid AS target, "
                    "r.fact AS fact, r.name AS name, r.valid_at AS valid_at, r.invalid_at AS invalid_at "
                    "LIMIT 400"
                )
        except Exception:
            log.exception("graphiti graph view failed agent=%s", agent_id)
            return empty_graph_view(self.name, agent_id)
        return build_graph_view(self.name, agent_id, _records(node_raw), _records(edge_raw))

    async def recall(self, req: memory_pb2.RecallRequest) -> memory_pb2.RecallResponse:
        started = time.time()
        limit = req.top_k or 5
        async with self._lock:
            group_id = _group_id(req.swarm_id, req.agent_id)
            edges = await self._graphiti.search(
                req.query_text,
                group_ids=[group_id],
                num_results=limit,
            )
            nodes = await _search_nodes(self._graphiti, req.query_text, group_id, limit)
        return assemble_response(
            req,
            graphiti_items(req.agent_id, nodes, edges),
            started=started,
        )

    async def write_episode(self, req: memory_pb2.WriteEpisodeRequest) -> str:
        episode = req.episode
        text = episode.summary.strip()
        if not text:
            return ""
        name = f"{episode.agent_id}:{episode.session_id}"
        group_id = _group_id(episode.swarm_id, episode.agent_id)
        async with self._lock:
            await self._add(name, text, group_id, episode.occurred_at_ms, message=True)
        return episode.session_id

    async def write_document(self, req: memory_pb2.WriteDocumentRequest) -> str:
        doc = req.document
        text = "\n".join(part for part in (doc.title, doc.uri, doc.content_ref) if part)
        if not text:
            return doc.doc_id
        project, _, _ = (doc.doc_id or "").partition("/")
        who = _group_id(project, doc.uploader_id or "documents")
        async with self._lock:
            await self._add(doc.doc_id or who, text, who, 0, message=False)
        return doc.doc_id

    async def _add(self, name: str, text: str, group_id: str, occurred_at_ms: int, *, message: bool) -> None:
        from graphiti_core.nodes import EpisodeType

        when = _when(occurred_at_ms)
        source = EpisodeType.message if message else EpisodeType.text
        await self._graphiti.add_episode(
            name=name or group_id,
            episode_body=text,
            source_description="termitaria",
            reference_time=when,
            source=source,
            group_id=group_id,
        )


def _group_id(swarm_id: str, agent_id: str) -> str:
    raw = f"{swarm_id}__{agent_id}" if swarm_id else agent_id
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", raw)
    return cleaned[:64] or "agent"


def _records(raw) -> list[dict]:
    if not raw:
        return []
    rows = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _when(occurred_at_ms: int) -> datetime:
    if occurred_at_ms > 0:
        return datetime.fromtimestamp(occurred_at_ms / 1000, tz=timezone.utc)
    return datetime.now(timezone.utc)


def _llm_client(config):
    # DeepSeek 这类兼容端点不收 json_schema，库文档要求改成 json_object。
    mode = "json_schema"
    base = config.base_url or ""
    if base and "api.openai.com" not in base:
        mode = "json_object"
    try:
        from graphiti_core.llm_client.openai_generic_client import OpenAIGenericClient

        return OpenAIGenericClient(config=config, structured_output_mode=mode)
    except ImportError:
        from graphiti_core.llm_client.openai_client import OpenAIClient

        return OpenAIClient(config=config)


def _cross_encoder(config):
    try:
        from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient

        return OpenAIRerankerClient(config=config)
    except ImportError:
        log.info("graphiti reranker import failed, using the library default")
        return None


def _local_embedder(model_name: str):
    from graphiti_core.embedder.client import EmbedderClient
    from sentence_transformers import SentenceTransformer

    class LocalEmbedder(EmbedderClient):
        def __init__(self) -> None:
            self._model = SentenceTransformer(model_name)
            self.embedding_dim = int(self._model.get_sentence_embedding_dimension())

        async def create(self, input_data):
            if isinstance(input_data, str):
                texts = [input_data]
            else:
                texts = [str(part) for part in input_data]
            vector = self._model.encode(texts[0], normalize_embeddings=True)
            return vector.tolist()

        async def create_batch(self, input_data_list):
            vectors = self._model.encode(list(input_data_list), normalize_embeddings=True)
            return [row.tolist() for row in vectors]

    return LocalEmbedder()


async def _search_nodes(graphiti, query: str, agent_id: str, limit: int) -> list:
    try:
        from graphiti_core.search.search_config_recipes import NODE_HYBRID_SEARCH_RRF
    except ImportError:
        log.info("graphiti node search recipe missing, recall returns edges only")
        return []
    try:
        found = await graphiti.search_(
            query,
            NODE_HYBRID_SEARCH_RRF,
            group_ids=[agent_id],
        )
    except Exception:
        log.exception("graphiti node search failed")
        return []
    nodes = list(getattr(found, "nodes", []) or [])
    return nodes[:limit]
