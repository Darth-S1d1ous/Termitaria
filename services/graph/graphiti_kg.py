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


_TEXT_LIMIT = {"DOCUMENT": 20000, "CONCEPT": 2000}


def to_subgraph(nodes: list[dict], edges: list[dict]) -> graph_pb2.QuerySubgraphResponse:
    resp = graph_pb2.QuerySubgraphResponse()
    ids: set[str] = set()
    for row in nodes:
        node_id = str(row.get("id") or "")
        if not node_id or node_id in ids:
            continue
        ids.add(node_id)
        name = str(row.get("name") or node_id)
        kind = str(row.get("kind") or "CONCEPT").upper()
        text = str(row.get("text") or row.get("summary") or "")
        props = Struct()
        if text:
            props.update({"text": text[: _TEXT_LIMIT.get(kind, 2000)]})
        resp.nodes.append(
            graph_pb2.Node(
                node_id=node_id,
                kind=graph_pb2.NODE_KIND_DOCUMENT if kind == "DOCUMENT" else graph_pb2.NODE_KIND_CONCEPT,
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


def select_subgraph(
    nodes: list[dict],
    edges: list[dict],
    seed_node_ids: list[str] | None = None,
    depth: int = 0,
    max_nodes: int = 0,
) -> tuple[list[dict], list[dict]]:
    """Pick the project graph or a BFS around the requested seeds.

    The project root seed returns the whole graph. A concrete node id walks
    ``depth`` hops over RELATES_TO. ``max_nodes`` caps the answer and documents
    are kept ahead of concepts when a cap is hit.
    """
    limit = max_nodes if max_nodes and max_nodes > 0 else 200
    by_id: dict[str, dict] = {}
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id or node_id in by_id:
            continue
        node["id"] = node_id
        by_id[node_id] = node
    roots = False
    starts: set[str] = set()
    for seed in seed_node_ids or []:
        seed = str(seed or "").strip()
        if not seed:
            continue
        local = seed.rsplit("/", 1)[-1]
        if local == "root":
            roots = True
        elif local in by_id:
            starts.add(local)
        elif seed in by_id:
            starts.add(seed)
    asked = bool(seed_node_ids)
    if roots or not asked:
        chosen = set(by_id)
    elif not starts:
        return [], []
    else:
        hops = depth if depth and depth > 0 else 4
        neighbours: dict[str, set[str]] = {}
        for edge in edges:
            src = str(edge.get("source") or "")
            dst = str(edge.get("target") or "")
            if src in by_id and dst in by_id:
                neighbours.setdefault(src, set()).add(dst)
                neighbours.setdefault(dst, set()).add(src)
        chosen = set(starts)
        frontier = set(starts)
        for _ in range(hops):
            step: set[str] = set()
            for node_id in frontier:
                step.update(neighbours.get(node_id, ()))
            step -= chosen
            if not step:
                break
            chosen.update(step)
            frontier = step
    if len(chosen) > limit:
        if roots or not asked:
            ordered = sorted(
                chosen,
                key=lambda node_id: (
                    0 if by_id[node_id].get("kind") == "DOCUMENT" else 1,
                    str(by_id[node_id].get("name") or node_id),
                ),
            )
        else:
            ordered = list(starts) + sorted(chosen - starts)
        chosen = set(ordered[:limit])
    kept_nodes = [node for node in by_id.values() if node["id"] in chosen]
    kept_nodes.sort(key=lambda node: (0 if node.get("kind") == "DOCUMENT" else 1, str(node.get("name") or node["id"])))
    edge_limit = max(400, limit * 4)
    kept_edges = []
    seen: set[str] = set()
    for edge in edges:
        src = str(edge.get("source") or "")
        dst = str(edge.get("target") or "")
        if src not in chosen or dst not in chosen or src == dst:
            continue
        edge_id = str(edge.get("id") or f"{src}/{edge.get('name') or ''}/{dst}")
        if edge_id in seen:
            continue
        seen.add(edge_id)
        kept_edges.append(edge)
        if len(kept_edges) >= edge_limit:
            break
    return kept_nodes, kept_edges


class IdleKnowledge:
    """Graphiti 没配上时不编一张假图。"""

    async def add_text(self, project: str, name: str, text: str) -> None:
        del name, text
        log.error("graphiti is not configured, document not extracted project=%s", project)

    async def subgraph(
        self,
        project: str,
        seed_node_ids: list[str] | None = None,
        depth: int = 0,
        max_nodes: int = 0,
    ) -> graph_pb2.QuerySubgraphResponse:
        del project, seed_node_ids, depth, max_nodes
        return graph_pb2.QuerySubgraphResponse()

    async def close(self) -> None:
        return


class GraphitiKnowledge:
    def __init__(self, graphiti, driver) -> None:
        self._graphiti = graphiti
        self._driver = driver
        self._lock = asyncio.Lock()
        self._ready = False

    async def open(self) -> None:
        async with self._lock:
            if self._ready:
                return
            await self._driver.health_check()
            self._ready = True

    async def close(self) -> None:
        await self._driver.close()

    async def _use(self, project: str):
        driver = self._driver.clone(database=group_id(project))
        await driver.ensure_database_initialized()
        return driver

    async def add_text(self, project: str, name: str, text: str) -> None:
        body = text.strip()
        if not project or not body:
            return
        from graphiti_core.nodes import EpisodeType

        await self.open()
        async with self._lock:
            await self._use(project)
            await self._graphiti.add_episode(
                name=name or project,
                episode_body=body,
                source_description="termitaria",
                reference_time=datetime.now(timezone.utc),
                source=EpisodeType.text,
                group_id=group_id(project),
                update_communities=False,
            )

    async def subgraph(
        self,
        project: str,
        seed_node_ids: list[str] | None = None,
        depth: int = 0,
        max_nodes: int = 0,
    ) -> graph_pb2.QuerySubgraphResponse:
        if not project:
            return graph_pb2.QuerySubgraphResponse()
        try:
            driver = await self._use(project)
            entity_raw = await driver.execute_query(
                "MATCH (n:Entity) RETURN n.uuid AS id, n.name AS name, n.summary AS summary"
            )
            episode_raw = await driver.execute_query(
                "MATCH (e:Episodic) RETURN e.uuid AS id, e.name AS name, e.content AS content"
            )
            edge_raw = await driver.execute_query(
                "MATCH (a:Entity)-[r:RELATES_TO]->(b:Entity) "
                "WHERE r.invalid_at IS NULL "
                "RETURN r.uuid AS id, a.uuid AS source, b.uuid AS target, "
                "r.name AS name, r.fact AS fact"
            )
        except Exception:
            log.info("graphiti graph is empty project=%s", project)
            return graph_pb2.QuerySubgraphResponse()
        nodes = [
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "summary": str(row.get("summary") or ""),
                "kind": "CONCEPT",
            }
            for row in _rows(entity_raw)
        ]
        nodes.extend(
            {
                "id": str(row.get("id") or ""),
                "name": str(row.get("name") or ""),
                "text": str(row.get("content") or ""),
                "kind": "DOCUMENT",
            }
            for row in _rows(episode_raw)
        )
        kept_nodes, kept_edges = select_subgraph(
            nodes,
            _rows(edge_raw),
            seed_node_ids=list(seed_node_ids or []),
            depth=depth,
            max_nodes=max_nodes,
        )
        return to_subgraph(kept_nodes, kept_edges)


def _rows(raw) -> list[dict]:
    if not raw:
        return []
    rows = raw[0] if isinstance(raw, tuple) else raw
    if not isinstance(rows, list):
        return []
    return [row for row in rows if isinstance(row, dict)]


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
        from graphiti_core.llm_client.config import LLMConfig

        from services.graph.falkor import MAX_COROUTINES, open_driver
        from services.memory.graphiti_backend import _cross_encoder, _llm_client, _local_embedder
    except Exception:
        log.exception("graphiti import failed")
        return IdleKnowledge()
    os.environ["EMBEDDING_DIM"] = "384"
    llm_config = LLMConfig(api_key=key, model=model, small_model=model, base_url=base)
    driver = open_driver(host, port)
    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=_llm_client(llm_config),
        embedder=_local_embedder(os.environ.get("MEMORY_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")),
        cross_encoder=_cross_encoder(llm_config),
        max_coroutines=MAX_COROUTINES,
    )
    log.info("graph store: graphiti falkordb %s:%s model=%s", host, port, model)
    return GraphitiKnowledge(graphiti, driver)
