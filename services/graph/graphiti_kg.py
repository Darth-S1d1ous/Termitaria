"""项目知识图。每个 project 一张 Graphiti 图，图存在 FalkorDB。

Graphiti 来自 pip 包 graphiti-core（uv sync --extra memory）。
文档和工具写来的正文交给 Graphiti。它抽出实体，并给每条边一个名字和一句事实。
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

from google.protobuf.struct_pb2 import Struct

from termitaria.graph.v1 import graph_pb2

log = logging.getLogger(__name__)


def project_of(node_id: str) -> str:
    project, sep, local = node_id.partition("/")
    if not sep or not project or not local or "/" in project:
        return ""
    return project


def group_id(project: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", project).strip("_")
    return (cleaned or "project")[:64]


def project_from_seeds(seeds: list[str]) -> str:
    for seed in seeds:
        project = project_of(seed)
        if project:
            return project
    return ""


def to_subgraph(nodes: list[dict], edges: list[dict]) -> graph_pb2.QuerySubgraphResponse:
    resp = graph_pb2.QuerySubgraphResponse()
    ids: set[str] = set()
    for row in nodes:
        node_id = str(row.get("id") or "")
        if not node_id or node_id in ids:
            continue
        ids.add(node_id)
        name = str(row.get("name") or node_id)
        summary = str(row.get("summary") or "")
        props = Struct()
        if summary:
            props.update({"text": summary[:2000]})
        resp.nodes.append(
            graph_pb2.Node(
                node_id=node_id,
                kind=graph_pb2.NODE_KIND_CONCEPT,
                title=name[:200],
                props=props,
            )
        )
    for row in edges:
        src = str(row.get("source") or "")
        dst = str(row.get("target") or "")
        relation = str(row.get("name") or "").strip()
        if not relation or src not in ids or dst not in ids or src == dst:
            continue
        resp.edges.append(
            graph_pb2.Edge(
                edge_id=str(row.get("id") or f"{src}/{relation}/{dst}"),
                from_node_id=src,
                to_node_id=dst,
                relation=relation,
                fact=str(row.get("fact") or "").strip(),
            )
        )
    return resp


class IdleKnowledge:
    """Graphiti 没配上时不编一张假图。"""

    async def add_text(self, project: str, name: str, text: str) -> None:
        del name, text
        log.error("graphiti is not configured, document not extracted project=%s", project)

    def note(self, project: str, name: str, text: str) -> None:
        task = asyncio.create_task(self.add_text(project, name, text))
        task.add_done_callback(_log_task)

    async def subgraph(self, project: str) -> graph_pb2.QuerySubgraphResponse:
        del project
        return graph_pb2.QuerySubgraphResponse()


class GraphitiKnowledge:
    def __init__(self, graphiti, driver) -> None:
        self._graphiti = graphiti
        self._driver = driver
        self._lock = asyncio.Lock()
        self._ready = False
        self._pending: set[asyncio.Task] = set()

    async def open(self) -> None:
        async with self._lock:
            if self._ready:
                return
            await self._graphiti.build_indices_and_constraints()
            self._ready = True

    async def add_text(self, project: str, name: str, text: str) -> None:
        body = text.strip()
        if not project or not body:
            return
        from graphiti_core.nodes import EpisodeType

        await self.open()
        async with self._lock:
            await self._graphiti.add_episode(
                name=name or project,
                episode_body=body,
                source_description="termitaria",
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.text,
                group_id=group_id(project),
                update_communities=False,
            )

    def note(self, project: str, name: str, text: str) -> None:
        task = asyncio.create_task(self.add_text(project, name, text))
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)
        task.add_done_callback(_log_task)

    async def subgraph(self, project: str) -> graph_pb2.QuerySubgraphResponse:
        if not project:
            return graph_pb2.QuerySubgraphResponse()
        driver = self._driver.clone(database=group_id(project))
        try:
            node_raw = await driver.execute_query(
                "MATCH (n:Entity) RETURN n.uuid AS id, n.name AS name, n.summary AS summary LIMIT 80"
            )
            edge_raw = await driver.execute_query(
                "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
                "WHERE r.invalid_at IS NULL "
                "RETURN r.uuid AS id, a.uuid AS source, b.uuid AS target, "
                "r.name AS name, r.fact AS fact LIMIT 200"
            )
        except Exception:
            log.info("graphiti graph is empty project=%s", project)
            return graph_pb2.QuerySubgraphResponse()
        return to_subgraph(_rows(node_raw), _rows(edge_raw))


def _rows(raw) -> list[dict]:
    if not raw:
        return []
    rows = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


def _log_task(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.error("graphiti extract failed: %s", exc)


def node_text(node: graph_pb2.Node) -> str:
    if node.props and "text" in node.props:
        return str(node.props["text"]).strip()
    return ""


def llm_settings() -> tuple[str, str, str]:
    provider = os.environ.get("MODEL_PROVIDER", "").strip().lower()
    if provider in {"", "deepseek"}:
        key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("MEMORY_LLM_API_KEY") or ""
        base = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        model = os.environ.get("DEEPSEEK_MODEL", "deepseek-flash").strip() or "deepseek-flash"
        return key, base, model
    key = os.environ.get("MEMORY_LLM_API_KEY") or os.environ.get("NEBIUS_API_KEY") or ""
    base = (
        os.environ.get("MEMORY_LLM_BASE_URL")
        or os.environ.get("NEBIUS_BASE_URL")
        or "https://api.openai.com/v1"
    ).rstrip("/")
    model = os.environ.get("MEMORY_LLM_MODEL", "").strip()
    return key, base, model


def open_graphiti(host: str, port: int):
    key, base, model = llm_settings()
    if not key or not model:
        log.error("graphiti needs an API key and a model")
        return IdleKnowledge()
    try:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.llm_client.config import LLMConfig

        from services.memory.graphiti_backend import _cross_encoder, _llm_client, _local_embedder
    except Exception:
        log.exception("graphiti import failed")
        return IdleKnowledge()
    os.environ["EMBEDDING_DIM"] = "384"
    llm_config = LLMConfig(api_key=key, model=model, small_model=model, base_url=base)
    driver = FalkorDriver(host=host, port=port)
    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=_llm_client(llm_config),
        embedder=_local_embedder(os.environ.get("MEMORY_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")),
        cross_encoder=_cross_encoder(llm_config),
    )
    log.info("graph store: graphiti falkordb %s:%s model=%s", host, port, model)
    return GraphitiKnowledge(graphiti, driver)
