"""agent 记忆按 agent_id 隔离，文档不进这套存储。"""
from __future__ import annotations

import asyncio

import services
from termitaria.memory.v1 import memory_pb2

from services.memory.agent_backend import AgentBackend, LocalVectors, MemoryRows, hash_embed
from services.memory.config import MemoryConfig


class _Embed:
    dims = 32

    def embed(self, text: str) -> list[float]:
        return hash_embed(text, self.dims)


def _backend() -> AgentBackend:
    cfg = MemoryConfig(nats_url="nats://localhost:4222", backend="agent")
    backend = AgentBackend(cfg, rows=MemoryRows(), vectors=LocalVectors(), embed=_Embed())
    return backend


def test_recall_stays_inside_one_agent():
    backend = _backend()

    async def run() -> None:
        await backend.write_episode(
            memory_pb2.WriteEpisodeRequest(
                episode=memory_pb2.Episode(
                    agent_id="researcher",
                    session_id="s1",
                    summary="Northwire quoted 4.80 dollars per kilogram for busbar wire.",
                )
            )
        )
        await backend.write_episode(
            memory_pb2.WriteEpisodeRequest(
                episode=memory_pb2.Episode(
                    agent_id="critic",
                    session_id="s1",
                    summary="The critic wants a source for every number.",
                )
            )
        )
        researcher = await backend.recall(
            memory_pb2.RecallRequest(agent_id="researcher", query_text="busbar wire price", top_k=5)
        )
        critic = await backend.recall(
            memory_pb2.RecallRequest(agent_id="critic", query_text="busbar wire price", top_k=5)
        )
        assert [item.agent_id for item in researcher.items] == ["researcher"]
        assert "4.80" in researcher.items[0].text
        assert all(item.agent_id == "critic" for item in critic.items)
        assert all("4.80" not in item.text for item in critic.items)
        empty = await backend.write_document(
            memory_pb2.WriteDocumentRequest(
                document=memory_pb2.Document(doc_id="p1/memo", title="memo", content_ref="body")
            )
        )
        assert empty == ""
        view = await backend.graph_view("researcher")
        summaries = [node["summary"] for node in view["nodes"]]
        assert any("4.80" in summary for summary in summaries)
        assert all("critic" not in (node.get("summary") or "") for node in view["nodes"])

    asyncio.run(run())


def test_recall_filters_episodes_by_project():
    backend = _backend()

    async def run() -> None:
        for project, session, text in (
            ("p_glossary", "s_glossary", "Glossary project hypothesis about definitions."),
            ("p_table", "s_table", "Table-first project hypothesis about invented numbers."),
        ):
            await backend.write_episode(
                memory_pb2.WriteEpisodeRequest(
                    episode=memory_pb2.Episode(
                        agent_id="ideator",
                        session_id=session,
                        summary=text,
                        project_id=project,
                    )
                )
            )
        glossary = await backend.recall(
            memory_pb2.RecallRequest(
                agent_id="ideator",
                project_id="p_glossary",
                query_text="hypothesis",
                top_k=5,
            )
        )
        assert [item.project_id for item in glossary.items] == ["p_glossary"]
        assert "definitions" in glossary.items[0].text
        table = await backend.recall(
            memory_pb2.RecallRequest(
                agent_id="ideator",
                project_id="p_table",
                query_text="hypothesis",
                top_k=5,
            )
        )
        assert [item.project_id for item in table.items] == ["p_table"]
        both = await backend.recall(
            memory_pb2.RecallRequest(agent_id="ideator", query_text="hypothesis", top_k=5)
        )
        assert {item.project_id for item in both.items} == {"p_glossary", "p_table"}

    asyncio.run(run())
