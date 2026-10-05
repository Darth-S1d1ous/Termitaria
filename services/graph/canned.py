"""罐装图：4 种节点 × 5 种边，内核落地前 QuerySubgraph 的过滤语义可测。

同事接手时本文件只留作 fixture（测试仍加载它），服务默认不再装载。
    gn_doc ──CITES──► gn_paper ──DEPENDS_ON──► gn_concept
                         ▲                         ▲
                         │ DERIVES_FROM            │ SUPPORTS
                      gn_claim ◄──CONTRADICTS── gn_claim_contra
"""
from __future__ import annotations

from google.protobuf.struct_pb2 import Struct

from termitaria.graph.v1 import graph_pb2

from .store import GraphStore

DOC = "gn_doc"
PAPER = "gn_paper"
CLAIM = "gn_claim"
CLAIM_CONTRA = "gn_claim_contra"
CONCEPT = "gn_concept"


def _props(data: dict) -> Struct:
    s = Struct()
    s.update(data)
    return s

def _node(node_id: str, kind: int, title: str, props: dict) -> graph_pb2.Node:
    return graph_pb2.Node(
        node_id=node_id, kind=kind, title=title, props=_props(props), created_at_ms=1
    )

def _edge(edge_id: str, src: str, dst: str, kind: int) -> graph_pb2.Edge:
    return graph_pb2.Edge(edge_id=edge_id, from_node_id=src, to_node_id=dst, kind=kind)

_NODES = (
    _node(DOC, graph_pb2.NODE_KIND_DOCUMENT, "Swarm memory notes", {"uploader": "u1"}),
    _node(PAPER, graph_pb2.NODE_KIND_PAPER, "Poincaré Embeddings",
          {"authors": "Nickel, Kiela", "year": 2017}),
    _node(CLAIM, graph_pb2.NODE_KIND_CLAIM,
          "Hyperbolic distance ranks abstract principles nearer the origin",
          {"doc_id": DOC}),
    _node(CLAIM_CONTRA, graph_pb2.NODE_KIND_CLAIM,
          "Euclidean embeddings are sufficient for hierarchical memory",
          {"doc_id": DOC}),
    _node(CONCEPT, graph_pb2.NODE_KIND_CONCEPT, "Poincaré ball", {}),
)

_EDGES = (
    _edge("ge_cites", DOC, PAPER, graph_pb2.EDGE_KIND_CITES),
    _edge("ge_derives", CLAIM, PAPER, graph_pb2.EDGE_KIND_DERIVES_FROM),
    _edge("ge_depends", PAPER, CONCEPT, graph_pb2.EDGE_KIND_DEPENDS_ON),
    _edge("ge_supports", CLAIM, CONCEPT, graph_pb2.EDGE_KIND_SUPPORTS),
    _edge("ge_contra", CLAIM_CONTRA, CLAIM, graph_pb2.EDGE_KIND_CONTRADICTS),
)

async def install(store: GraphStore) -> None:
    for node in _NODES:
        await store.upsert_node(node)
    for edge in _EDGES:
        await store.upsert_edge(edge)