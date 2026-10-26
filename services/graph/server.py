"""KnowledgeGraphService 的 grpc.aio 实现。图由 Graphiti 抽取。"""
from __future__ import annotations

import logging

import grpc

from termitaria.graph.v1 import graph_pb2, graph_pb2_grpc
from termitaria.memory.v1 import memory_pb2

from services.common import envelope

from .graphiti_kg import node_text, project_from_seeds, project_of

log = logging.getLogger(__name__)

_DOCUMENT = "memory.write.document"


class KnowledgeGraphServicer(graph_pb2_grpc.KnowledgeGraphServiceServicer):
    def __init__(self, engine, js=None) -> None:
        self._engine = engine
        self._js = js

    async def UpsertNode(self, request, context):
        node = request.node
        project = project_of(node.node_id)
        text = node_text(node)
        if project and text:
            if self._js is None:
                raise RuntimeError("knowledge graph ingest is not connected")
            req = memory_pb2.WriteDocumentRequest(
                document=memory_pb2.Document(
                    doc_id=node.node_id,
                    title=node.title or node.node_id,
                    content_ref=text,
                    mime_type="text/plain",
                    uploader_id="agent",
                )
            )
            await self._js.publish(_DOCUMENT, envelope.pack("graph-service", project, req))
        return graph_pb2.UpsertNodeResponse(node_id=node.node_id)

    async def UpsertEdge(self, request, context):
        # 边由 Graphiti 在抽实体时写下。调用方传来的结构边不进图。
        return graph_pb2.UpsertEdgeResponse(edge_id=request.edge.edge_id)

    async def GetNode(self, request, context):
        await context.abort(grpc.StatusCode.NOT_FOUND, request.node_id)

    async def QuerySubgraph(self, request, context):
        project = project_from_seeds(list(request.seed_node_ids))
        return await self._engine.subgraph(
            project,
            seed_node_ids=list(request.seed_node_ids),
            depth=int(request.depth),
            max_nodes=int(request.max_nodes),
        )


async def serve(cfg_addr: str, engine, nc=None):
    js = nc.jetstream() if nc is not None else None
    server = grpc.aio.server()
    graph_pb2_grpc.add_KnowledgeGraphServiceServicer_to_server(
        KnowledgeGraphServicer(engine, js), server
    )
    server.add_insecure_port(cfg_addr)
    await server.start()
    log.info("graph grpc up: %s", cfg_addr)
    return server
