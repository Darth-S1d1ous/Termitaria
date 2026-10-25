"""Graphiti 的实体和事实边映射成项目子图。不连 FalkorDB，不调模型。"""
from __future__ import annotations

import asyncio

import services
from termitaria.graph.v1 import graph_pb2

from services.graph.graphiti_kg import group_id, project_from_seeds, to_subgraph
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


def test_upsert_sends_text_to_graphiti_and_query_reads_that_project():
    class Engine:
        def __init__(self):
            self.notes = []

        def note(self, project, name, text):
            self.notes.append((project, name, text))

        async def subgraph(self, project):
            assert project == "p_desk"
            return to_subgraph(
                [{"id": "n1", "name": "Northwire", "summary": "A wire seller."}],
                [],
            )

    engine = Engine()
    servicer = KnowledgeGraphServicer(engine)
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
    assert engine.notes == [("p_desk", "Radar carrier", "The carrier is 77 GHz.")]
    assert resp.nodes[0].title == "Northwire"
