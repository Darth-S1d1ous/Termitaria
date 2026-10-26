"""Graphiti 的实体和事实边映射成项目子图。不连 FalkorDB，不调模型。"""
from __future__ import annotations

import asyncio

import services
from termitaria.graph.v1 import graph_pb2

from services.graph.graphiti_kg import group_id, project_from_seeds, select_subgraph, to_subgraph
from services.graph.server import KnowledgeGraphServicer


def test_group_id_keeps_a_project_token():
    assert group_id("p_desk") == "p_desk"
    assert group_id("p desk/north") == "p_desk_north"
    assert project_from_seeds(["p_desk/root"]) == "p_desk"


def test_subgraph_uses_graphiti_names_and_facts():
    resp = to_subgraph(
        [
            {"id": "n1", "name": "Northwire", "summary": "A wire seller."},
            {"id": "n2", "name": "4.80 dollars per kilogram", "summary": "The Houston quote."},
        ],
        [
            {
                "id": "e1",
                "source": "n1",
                "target": "n2",
                "name": "PRICED_AT",
                "fact": "Northwire priced busbar wire at 4.80 dollars per kilogram.",
            }
        ],
    )
    assert [node.title for node in resp.nodes] == ["Northwire", "4.80 dollars per kilogram"]
    assert resp.edges[0].relation == "PRICED_AT"
    assert resp.edges[0].fact.startswith("Northwire priced")


def test_episodes_map_to_document_nodes():
    resp = to_subgraph(
        [
            {"id": "d1", "name": "report.md", "text": "Report body", "kind": "DOCUMENT"},
            {"id": "n1", "name": "GPQA", "summary": "A benchmark.", "kind": "CONCEPT"},
        ],
        [],
    )
    assert resp.nodes[0].kind == graph_pb2.NODE_KIND_DOCUMENT
    assert resp.nodes[0].props["text"] == "Report body"
    assert resp.nodes[1].kind == graph_pb2.NODE_KIND_CONCEPT


def test_select_subgraph_keeps_documents_under_a_cap():
    nodes = [
        {"id": "d1", "name": "report.md", "text": "Report body", "kind": "DOCUMENT"},
        {"id": "n1", "name": "A", "summary": "a", "kind": "CONCEPT"},
        {"id": "n2", "name": "B", "summary": "b", "kind": "CONCEPT"},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "name": "LINKS", "fact": ""},
        {"id": "e2", "source": "d1", "target": "n1", "name": "MENTIONS", "fact": ""},
    ]
    kept, kept_edges = select_subgraph(nodes, edges, ["p/root"], depth=4, max_nodes=2)
    assert [node["id"] for node in kept] == ["d1", "n1"]
    assert [edge["id"] for edge in kept_edges] == ["e2"]


def test_select_subgraph_walks_from_a_seed():
    nodes = [
        {"id": "n1", "name": "A", "summary": "a", "kind": "CONCEPT"},
        {"id": "n2", "name": "B", "summary": "b", "kind": "CONCEPT"},
        {"id": "n3", "name": "C", "summary": "c", "kind": "CONCEPT"},
    ]
    edges = [
        {"id": "e1", "source": "n1", "target": "n2", "name": "LINKS", "fact": ""},
        {"id": "e2", "source": "n2", "target": "n3", "name": "LINKS", "fact": ""},
    ]
    kept, _ = select_subgraph(nodes, edges, ["p/n1"], depth=1)
    assert {node["id"] for node in kept} == {"n1", "n2"}
    empty, _ = select_subgraph(nodes, edges, ["p/missing"], depth=1)
    assert empty == []


def test_upsert_queues_a_document_and_query_reads_that_project():
    from services.common import envelope
    from termitaria.memory.v1 import memory_pb2

    class Engine:
        def __init__(self):
            self.notes = []

        async def add_text(self, project, name, text):
            self.notes.append((project, name, text))

        async def subgraph(self, project, seed_node_ids=None, depth=0, max_nodes=0):
            assert project == "p_desk"
            assert seed_node_ids == ["p_desk/root"]
            return to_subgraph(
                [{"id": "n1", "name": "Northwire", "summary": "A wire seller."}],
                [],
            )

    class JS:
        def __init__(self):
            self.sent = []

        async def publish(self, subject, payload):
            self.sent.append((subject, payload))

    engine = Engine()
    js = JS()
    servicer = KnowledgeGraphServicer(engine, js)
    node = graph_pb2.Node(node_id="p_desk/claim_1", title="Radar carrier", kind=graph_pb2.NODE_KIND_CLAIM)
    node.props.update({"text": "The carrier is 77 GHz."})

    async def run():
        await servicer.UpsertNode(graph_pb2.UpsertNodeRequest(node=node), None)
        resp = await servicer.QuerySubgraph(
            graph_pb2.QuerySubgraphRequest(seed_node_ids=["p_desk/root"]),
            None,
        )
        return resp

    resp = asyncio.run(run())
    assert engine.notes == []
    subject, payload = js.sent[0]
    assert subject == "memory.write.document"
    _, req = envelope.unpack(payload, memory_pb2.WriteDocumentRequest)
    assert req.document.doc_id == "p_desk/claim_1"
    assert req.document.title == "Radar carrier"
    assert req.document.content_ref == "The carrier is 77 GHz."
    assert resp.nodes[0].title == "Northwire"
