"""KnowledgeGraphService 的 grpc.aio 实现。存储经 GraphStore 注入。"""
from __future__ import annotations

import logging

import grpc

from termitaria.graph.v1 import graph_pb2, graph_pb2_grpc

from .store import GraphError, GraphStore

log = logging.getLogger(__name__)

_STATUS = {
    "GRAPH_NODE_NOT_FOUND": grpc.StatusCode.NOT_FOUND,
    "GRAPH_INVALID_EDGE": grpc.StatusCode.INVALID_ARGUMENT,
}

class KnowledgeGraphServicer(graph_pb2_grpc.KnowledgeGraphServiceServicer):
    def __init__(self, store: GraphStore) -> None:
        self._store = store

    async def UpsertNode(self, request, context):
        node_id = await self._store.upsert_node(request.node)
        return graph_pb2.UpsertNodeResponse(node_id=node_id)

    async def UpsertEdge(self, request, context):
        try:
            edge_id = await self._store.upsert_edge(request.edge)
        except GraphError as exc:
            await context.abort(_STATUS[exc.code], exc.code)
        return graph_pb2.UpsertEdgeResponse(edge_id=edge_id)

    async def GetNode(self, request, context):
        try:
            node = await self._store.get_node(request.node_id)
        except GraphError as exc:
            await context.abort(_STATUS[exc.code], exc.code)
        return graph_pb2.GetNodeResponse(node=node)
        
    async def QuerySubgraph(self, request, context):
        resp = await self._store.query_subgraph(
            list(request.seed_node_ids),
            request.depth,
            list(request.edge_kinds),
            request.max_nodes,
        )
        return resp

async def serve(cfg_addr: str, store: GraphStore):
    server = grpc.aio.server()
    graph_pb2_grpc.add_KnowledgeGraphServiceServicer_to_server(
        KnowledgeGraphServicer(store), server
    )
    server.add_insecure_port(cfg_addr)
    await server.start()
    log.info("graph grpc up: %s", cfg_addr)
    return server