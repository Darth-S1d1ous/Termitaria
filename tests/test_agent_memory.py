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

    asyncio.run(run())


def test_two_sentences_in_one_session_both_stay():
    backend = _backend()

    async def run() -> None:
        for text in (
            "We are working on Wi-Fi CSI breathing sensing.",
            "What are we working on?",
        ):
            await backend.write_episode(
                memory_pb2.WriteEpisodeRequest(
                    episode=memory_pb2.Episode(
                        agent_id="pi",
                        session_id="s1",
                        summary=text,
                    )
                )
            )
        recalled = await backend.recall(
            memory_pb2.RecallRequest(agent_id="pi", query_text="breathing sensing", top_k=5)
        )
        texts = [item.text for item in recalled.items]
        assert "We are working on Wi-Fi CSI breathing sensing." in texts
        assert "What are we working on?" in texts

    asyncio.run(run())


def test_recall_stays_inside_one_project():
    backend = _backend()

    async def run() -> None:
        for swarm, text in (
            ("wifi", "The topic is Wi-Fi sensing."),
            ("radar", "The topic is radar sensing."),
        ):
            await backend.write_episode(
                memory_pb2.WriteEpisodeRequest(
                    episode=memory_pb2.Episode(
                        agent_id="pi",
                        session_id=f"s-{swarm}",
                        swarm_id=swarm,
                        summary=text,
                    )
                )
            )
        wifi = await backend.recall(
            memory_pb2.RecallRequest(agent_id="pi", swarm_id="wifi", query_text="topic", top_k=5)
        )
        radar = await backend.recall(
            memory_pb2.RecallRequest(agent_id="pi", swarm_id="radar", query_text="topic", top_k=5)
        )
        assert [item.text for item in wifi.items] == ["The topic is Wi-Fi sensing."]
        assert [item.text for item in radar.items] == ["The topic is radar sensing."]

    asyncio.run(run())
