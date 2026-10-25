"""FalkorDB 上的知识图。一个 project 一张图，图名由 project id 得到。

节点 id 形如 `{project}/{local}`。查询只打开种子所属的那张图。
"""
from __future__ import annotations

import asyncio
import logging
import re
from urllib.parse import urlparse

from google.protobuf.struct_pb2 import Struct

from termitaria.graph.v1 import graph_pb2

from .store import GraphError, InMemoryGraph

log = logging.getLogger(__name__)

_LABEL = {
    graph_pb2.NODE_KIND_DOCUMENT: "Document",
    graph_pb2.NODE_KIND_PAPER: "Paper",
    graph_pb2.NODE_KIND_CLAIM: "Claim",
    graph_pb2.NODE_KIND_CONCEPT: "Concept",
}
_REL = {
    graph_pb2.EDGE_KIND_CITES: "CITES",
    graph_pb2.EDGE_KIND_DERIVES_FROM: "DERIVES_FROM",
    graph_pb2.EDGE_KIND_DEPENDS_ON: "DEPENDS_ON",
    graph_pb2.EDGE_KIND_SUPPORTS: "SUPPORTS",
    graph_pb2.EDGE_KIND_CONTRADICTS: "CONTRADICTS",
    graph_pb2.EDGE_KIND_RELATES: "RELATES",
}


def project_of(node_id: str) -> str:
    project, sep, local = node_id.partition("/")
    if not sep or not project or not local or "/" in project:
        return ""
    return project


def graph_name(project: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", project)
    return "kg_" + cleaned


def parse_falkor_url(url: str) -> tuple[str, int]:
    parsed = urlparse(url if "://" in url else "redis://" + url)
    return parsed.hostname or "localhost", parsed.port or 6379


class FalkorStore:
    def __init__(self, host: str, port: int) -> None:
        from falkordb import FalkorDB

        self._db = FalkorDB(host=host, port=port)
        self._lock = asyncio.Lock()

    def ping(self) -> None:
        self._db.select_graph("kg_ping").query("RETURN 1")

    async def upsert_node(self, node: graph_pb2.Node) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._upsert_node, node)

    async def upsert_edge(self, edge: graph_pb2.Edge) -> str:
        async with self._lock:
            return await asyncio.to_thread(self._upsert_edge, edge)

    async def get_node(self, node_id: str) -> graph_pb2.Node:
        async with self._lock:
            return await asyncio.to_thread(self._get_node, node_id)

    async def query_subgraph(
        self,
        seed_node_ids: list[str],
        depth: int,
        edge_kinds: list[int],
        max_nodes: int,
    ) -> graph_pb2.QuerySubgraphResponse:
        async with self._lock:
            return await asyncio.to_thread(
                self._query, seed_node_ids, depth, edge_kinds, max_nodes
            )

    def _graph(self, project: str):
        return self._db.select_graph(graph_name(project))

    def _upsert_node(self, node: graph_pb2.Node) -> str:
        project = project_of(node.node_id)
        if not project:
            raise GraphError("GRAPH_INVALID_EDGE", "node id needs a project prefix")
        label = _LABEL.get(node.kind) or "Concept"
        text = ""
        if node.props and "text" in node.props:
            text = str(node.props["text"])
        self._graph(project).query(
            f"""
            MERGE (n:{label} {{node_id: $id}})
            SET n.kind = $kind, n.title = $title, n.uri = $uri,
                n.text = $text, n.created_at_ms = $created
            """,
            {
                "id": node.node_id,
                "kind": int(node.kind),
                "title": node.title,
                "uri": node.uri,
                "text": text,
                "created": int(node.created_at_ms),
            },
        )
        return node.node_id

    def _upsert_edge(self, edge: graph_pb2.Edge) -> str:
        src = project_of(edge.from_node_id)
        dst = project_of(edge.to_node_id)
        if not src or src != dst:
            raise GraphError("GRAPH_INVALID_EDGE", "edge must stay inside one project")
        if edge.from_node_id == edge.to_node_id:
            raise GraphError("GRAPH_INVALID_EDGE", "self-loop rejected")
        rel = _REL.get(edge.kind)
        if not rel or not edge.edge_id:
            raise GraphError("GRAPH_INVALID_EDGE", "edge kind and id are required")
        relation, fact = _edge_text(edge)
        found = self._graph(src).query(
            f"""
            MATCH (a {{node_id: $src}}), (b {{node_id: $dst}})
            MERGE (a)-[r:{rel} {{edge_id: $id}}]->(b)
            SET r.kind = $kind, r.relation = $relation, r.fact = $fact
            RETURN r.edge_id
            """,
            {
                "src": edge.from_node_id,
                "dst": edge.to_node_id,
                "id": edge.edge_id,
                "kind": int(edge.kind),
                "relation": relation,
                "fact": fact,
            },
        )
        if not found.result_set:
            raise GraphError("GRAPH_INVALID_EDGE", "dangling edge rejected")
        return edge.edge_id

    def _get_node(self, node_id: str) -> graph_pb2.Node:
        project = project_of(node_id)
        if not project:
            raise GraphError("GRAPH_NODE_NOT_FOUND", node_id)
        found = self._graph(project).query(
            """
            MATCH (n {node_id: $id})
            RETURN n.node_id, n.kind, n.title, n.uri, n.text, n.created_at_ms
            """,
            {"id": node_id},
        )
        if not found.result_set:
            raise GraphError("GRAPH_NODE_NOT_FOUND", node_id)
        return _node(found.result_set[0])

    def _query(self, seeds, depth, edge_kinds, max_nodes) -> graph_pb2.QuerySubgraphResponse:
        projects = []
        for seed in seeds:
            project = project_of(seed)
            if project and project not in projects:
                projects.append(project)
        merged = graph_pb2.QuerySubgraphResponse()
        seen: set[str] = set()
        for project in projects:
            part = self._query_project(project, seeds, depth, edge_kinds, max_nodes)
            for node in part.nodes:
                if node.node_id in seen:
                    continue
                seen.add(node.node_id)
                merged.nodes.append(node)
            for edge in part.edges:
                merged.edges.append(edge)
        return merged

    def _query_project(self, project, seeds, depth, edge_kinds, max_nodes):
        graph = self._graph(project)
        try:
            nodes = graph.query(
                "MATCH (n) RETURN n.node_id, n.kind, n.title, n.uri, n.text, n.created_at_ms"
            ).result_set
            edges = graph.query(
                """
                MATCH (a)-[r]->(b)
                RETURN r.edge_id, a.node_id, b.node_id, r.kind, r.relation, r.fact
                """
            ).result_set
        except Exception:
            log.info("project graph %s is empty", project)
            return graph_pb2.QuerySubgraphResponse()
        memory = InMemoryGraph()
        for row in nodes or []:
            node = _node(row)
            if project_of(node.node_id) != project:
                continue
            memory._nodes[node.node_id] = node
            memory._adj.setdefault(node.node_id, [])
        for row in edges or []:
            edge = _edge(row)
            if project_of(edge.from_node_id) != project or project_of(edge.to_node_id) != project:
                continue
            if edge.from_node_id not in memory._nodes or edge.to_node_id not in memory._nodes:
                continue
            memory._edges[edge.edge_id] = edge
            memory._adj.setdefault(edge.from_node_id, []).append((edge.to_node_id, edge.edge_id))
            memory._adj.setdefault(edge.to_node_id, []).append((edge.from_node_id, edge.edge_id))
        own = [seed for seed in seeds if project_of(seed) == project]

        async def run():
            return await memory.query_subgraph(own, depth, edge_kinds, max_nodes)

        return asyncio.run(run())


def _text(value) -> str:
    if value is None:
        return ""
    return str(value)


def _num(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _node(row) -> graph_pb2.Node:
    props = Struct()
    text = _text(row[4])
    if text:
        props.update({"text": text})
    return graph_pb2.Node(
        node_id=_text(row[0]),
        kind=_num(row[1]),
        title=_text(row[2]),
        uri=_text(row[3]),
        props=props,
        created_at_ms=_num(row[5]),
    )


def _edge_text(edge: graph_pb2.Edge) -> tuple[str, str]:
    relation = ""
    fact = ""
    if edge.props:
        if "relation" in edge.props:
            relation = str(edge.props["relation"])
        if "fact" in edge.props:
            fact = str(edge.props["fact"])
    return relation, fact


def _edge(row) -> graph_pb2.Edge:
    props = Struct()
    fields = {}
    if len(row) > 4 and _text(row[4]):
        fields["relation"] = _text(row[4])
    if len(row) > 5 and _text(row[5]):
        fields["fact"] = _text(row[5])
    if fields:
        props.update(fields)
    return graph_pb2.Edge(
        edge_id=_text(row[0]),
        from_node_id=_text(row[1]),
        to_node_id=_text(row[2]),
        kind=_num(row[3]),
        props=props,
    )
