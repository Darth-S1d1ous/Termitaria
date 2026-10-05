"""图存储接缝。同事用 FalkorStore 替换 InMemoryGraph，方法签名保持不变。

stub 语义（P0 提议，conformance 守门）：
- node_id / edge_id 留空则服务端分配；相同 id 再次 upsert 为替换；
- 悬空边、自环拒绝；
- QuerySubgraph：depth=0 只回种子；双向遍历；max_nodes=0 视为 64；
  截断按 BFS 序（邻居按 node_id 排序）；返回节点集的诱导子图。
"""
from __future__ import annotations

import asyncio
from typing import Protocol

import ulid

from termitaria.graph.v1 import graph_pb2

from services.common import envelope

DEFAULT_MAX_NODES = 64


class GraphError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code  # GRAPH_NODE_NOT_FOUND / GRAPH_INVALID_EDGE

class GraphStore(Protocol):
    async def upsert_node(self, node: graph_pb2.Node) -> str: ...
    async def upsert_edge(self, edge: graph_pb2.Edge) -> str: ...
    async def get_node(self, node_id: str) -> graph_pb2.Node: ...
    async def query_subgraph(
        self,
        seed_node_ids: list[str],
        depth: int,
        edge_kinds: list[int],
        max_nodes: int,
    ) -> graph_pb2.QuerySubgraphResponse: ...

class InMemoryGraph:
    def __init__(self) -> None:
        self._nodes: dict[str, graph_pb2.Node] = {}
        self._edges: dict[str, graph_pb2.Edge] = {}
        self._adj: dict[str, list[tuple[str, str]]] = {}  # node_id → [(neighbor, edge_id)]
        self._lock = asyncio.Lock()

    async def upsert_node(self, node: graph_pb2.Node) -> str:
        async with self._lock:
            stored = graph_pb2.Node()
            stored.CopyFrom(node)
            if not stored.node_id:
                stored.node_id = "gn_" + str(ulid.new())
            prev = self._nodes.get(stored.node_id)
            if prev is not None:
                stored.created_at_ms = prev.created_at_ms
            elif not stored.created_at_ms:
                stored.created_at_ms = envelope.now_ms()
            self._nodes[stored.node_id] = stored
            self._adj.setdefault(stored.node_id, [])
            return stored.node_id

    async def upsert_edge(self, edge: graph_pb2.Edge) -> str:
        async with self._lock:
            if not edge.from_node_id or not edge.to_node_id:
                raise GraphError("GRAPH_INVALID_EDGE", "edge endpoints required")
            if edge.from_node_id == edge.to_node_id:
                raise GraphError("GRAPH_INVALID_EDGE", "self-loop rejected")
            if edge.from_node_id not in self._nodes or edge.to_node_id not in self._nodes:
                raise GraphError("GRAPH_INVALID_EDGE", "dangling edge rejected")
            stored = graph_pb2.Edge()
            stored.CopyFrom(edge)
            if not stored.edge_id:
                stored.edge_id = "ge_" + str(ulid.new())
            self._unlink(stored.edge_id)
            self._edges[stored.edge_id] = stored
            self._adj.setdefault(stored.from_node_id, []).append((stored.to_node_id, stored.edge_id))
            self._adj.setdefault(stored.to_node_id, []).append((stored.from_node_id, stored.edge_id))

    async def get_node(self, node_id: str) -> graph_pb2.Node:
        async with self._lock:
            node = self._nodes.get(node_id)
            if node is None:
                raise GraphError("GRAPH_NODE_NOT_FOUND", node_id)
            out = graph_pb2.Node()
            out.CopyFrom(node)
            return out

    async def query_subgraph(
        self,
        seed_node_ids: list[str],
        depth: int,
        edge_kinds: list[int],
        max_nodes: int,
    ) -> graph_pb2.QuerySubgraphResponse:
        async with self._lock:
            cap = max_nodes or DEFAULT_MAX_NODES
            allowed = set(edge_kinds) or None
            ordered: list[str] = []
            seen: set[str] = set()
            for seed in seed_node_ids:
                if seed in self._nodes and seed not in seen:
                    ordered.append(seed)
                    seen.add(seed)
                    if len(ordered) >= cap:
                        break
            frontier = list(ordered)
            for _ in range(depth):
                nxt: list[str] = []
                for nid in frontier:
                    for neighbor, edge_id in self._neighbors(nid, allowed):
                        if neighbor in seen or neighbor not in self._nodes:
                            continue
                        ordered.append(neighbor)
                        seen.add(neighbor)
                        nxt.append(neighbor)
                        if len(ordered) >= cap:
                            break
                    if len(ordered) >= cap:
                        break
                frontier = nxt
                if len(ordered) >= cap:
                    break
            edges = [
                self._copy_edge(e)
                for e in sorted(self._edges.values(), key=lambda e: e.edge_id)
                if e.from_node_id in seen and e.to_node_id in seen
                and (allowed is None or e.kind in allowed)
            ]
            return graph_pb2.QuerySubgraphResponse(
                nodes=[self._copy_node(self._nodes[n]) for n in ordered],
                edges=edges,
            )

    def _neighbors(self, node_id: str, allowed: set[int] | None) -> list[tuple[str, str]]:
        out = []
        for neighbor, edge_id in self._adj.get(node_id, []):
            edge = self._edges.get(edge_id)
            if edge is None:
                continue
            if allowed is not None and edge.kind not in allowed:
                continue
            out.append((neighbor, edge_id))
        out.sort()
        return out
    
    def _unlink(self, edge_id: str) -> None:
        prev = self._edges.get(edge_id)
        if prev is None:
            return
        for end in (prev.from_node_id, prev.to_node_id):
            self._adj[end] = [(n, e) for n, e in self._adj.get(end, []) if e != edge_id]
    
    @staticmethod
    def _copy_node(node: graph_pb2.Node) -> graph_pb2.Node:
        out = graph_pb2.Node()
        out.CopyFrom(node)
        return out
    
    @staticmethod
    def _copy_edge(edge: graph_pb2.Edge) -> graph_pb2.Edge:
        out = graph_pb2.Edge()
        out.CopyFrom(edge)
        return out