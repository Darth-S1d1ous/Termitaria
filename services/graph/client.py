"""worker 用的薄客户端（P2 接线）。KG 不可达时返回空子图，不抛错。"""
from __future__ import annotations

import logging

import grpc

from termitaria.graph.v1 import graph_pb2, graph_pb2_grpc

log = logging.getLogger(__name__)

class GraphClient:
    def __init__(self, addr: str, timeout_s: float = 0.2) -> None:
        self._timeout = timeout_s
        self._channel = grpc.aio.insecure_channel(addr)
        self._stub = graph_pb2_grpc.KnowledgeGraphServiceStub(self._channel)

    async def query_subgraph(
        self,
        seed_node_ids: list[str],
        depth: int = 1,
        relations: list[str] | None = None,
        max_nodes: int = 32,
        strict: bool = False,
    ) -> graph_pb2.QuerySubgraphResponse:
        if not seed_node_ids:
            return graph_pb2.QuerySubgraphResponse()
        req = graph_pb2.QuerySubgraphRequest(
            seed_node_ids=seed_node_ids,
            depth=depth,
            relations=relations or [],
            max_nodes=max_nodes,
        )
        try:
            return await self._stub.QuerySubgraph(req, timeout=self._timeout)
        except Exception:
            if strict:
                raise
            log.warning("graph query degraded: seeds=%s", seed_node_ids, exc_info=True)
            return graph_pb2.QuerySubgraphResponse()

    async def upsert_node(self, node: graph_pb2.Node) -> str:
        resp = await self._stub.UpsertNode(
            graph_pb2.UpsertNodeRequest(node=node),
            timeout=self._timeout,
        )
        return resp.node_id

    async def upsert_edge(self, edge: graph_pb2.Edge) -> str:
        resp = await self._stub.UpsertEdge(
            graph_pb2.UpsertEdgeRequest(edge=edge),
            timeout=self._timeout,
        )
        return resp.edge_id

    async def close(self) -> None:
        await self._channel.close()