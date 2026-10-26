"""一个 agent 一份记忆。正文在 Postgres，episode 向量在 Qdrant。

召回必须带 agent_id。查询只过滤这一个 agent，不读知识图，也不读其他 agent。
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
import uuid

from termitaria.memory.v1 import memory_pb2

from .config import MemoryConfig
from .graph_view import build_graph_view
from .items import assemble_response
from .timing import time_warm_queries

_STABLE = (
    memory_pb2.MEMORY_KIND_ROLE,
    memory_pb2.MEMORY_KIND_RULE,
    memory_pb2.MEMORY_KIND_PRINCIPLE,
)


def hash_embed(text: str, dims: int = 32) -> list[float]:
    vec = [0.0] * dims
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        n = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        vec[n % dims] += 1.0
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


def _point_id(item_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, item_id))


class MemoryRows:
    def __init__(self) -> None:
        self._rows: dict[str, dict] = {}

    def upsert(self, row: dict) -> None:
        self._rows[row["item_id"]] = dict(row)

    def list_agent(self, agent_id: str) -> list[dict]:
        return [dict(row) for row in self._rows.values() if row["agent_id"] == agent_id]


class PostgresRows:
    def __init__(self, dsn: str) -> None:
        import psycopg

        self._conn = psycopg.connect(dsn, autocommit=True)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_memory (
              item_id TEXT PRIMARY KEY,
              agent_id TEXT NOT NULL,
              kind INT NOT NULL,
              text TEXT NOT NULL,
              score DOUBLE PRECISION NOT NULL DEFAULT 0,
              source_session_id TEXT NOT NULL DEFAULT '',
              occurred_at_ms BIGINT NOT NULL DEFAULT 0,
              project_id TEXT NOT NULL DEFAULT ''
            )
            """
        )
        # 旧库升级：保留已有行，空 project_id 的 episode 不再进入按 project 的召回。
        self._conn.execute(
            "ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS project_id TEXT NOT NULL DEFAULT ''"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS agent_memory_agent_project ON agent_memory (agent_id, project_id)"
        )

    def upsert(self, row: dict) -> None:
        self._conn.execute(
            """
            INSERT INTO agent_memory
              (item_id, agent_id, kind, text, score, source_session_id, occurred_at_ms, project_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (item_id) DO UPDATE SET
              text = EXCLUDED.text,
              score = EXCLUDED.score,
              occurred_at_ms = EXCLUDED.occurred_at_ms,
              project_id = EXCLUDED.project_id
            """,
            (
                row["item_id"],
                row["agent_id"],
                row["kind"],
                row["text"],
                row["score"],
                row["source_session_id"],
                row["occurred_at_ms"],
                row.get("project_id", ""),
            ),
        )

    def list_agent(self, agent_id: str) -> list[dict]:
        cur = self._conn.execute(
            """
            SELECT item_id, agent_id, kind, text, score, source_session_id, occurred_at_ms, project_id
            FROM agent_memory WHERE agent_id = %s
            """,
            (agent_id,),
        )
        cols = ["item_id", "agent_id", "kind", "text", "score", "source_session_id", "occurred_at_ms", "project_id"]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


class LocalVectors:
    def __init__(self) -> None:
        self._points: list[dict] = []

    def upsert(self, point: dict) -> None:
        self._points = [p for p in self._points if p["item_id"] != point["item_id"]]
        self._points.append(dict(point))

    def search(self, agent_id: str, vector: list[float], limit: int, project_id: str = "") -> list[dict]:
        scored = []
        for point in self._points:
            if point["agent_id"] != agent_id:
                continue
            if project_id and point.get("project_id") != project_id:
                continue
            score = sum(a * b for a, b in zip(vector, point["vector"]))
            scored.append((score, point))
        scored.sort(key=lambda item: item[0], reverse=True)
        out = []
        for score, point in scored[:limit]:
            hit = dict(point)
            hit["score"] = score
            out.append(hit)
        return out


class QdrantVectors:
    def __init__(self, host: str, port: int, collection: str, dims: int) -> None:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams

        self._client = QdrantClient(host=host, port=port)
        self._collection = collection
        if not self._client.collection_exists(collection):
            self._client.create_collection(
                collection_name=collection,
                vectors_config=VectorParams(size=dims, distance=Distance.COSINE),
            )

    def upsert(self, point: dict) -> None:
        from qdrant_client.models import PointStruct

        self._client.upsert(
            collection_name=self._collection,
            points=[
                PointStruct(
                    id=_point_id(point["item_id"]),
                    vector=point["vector"],
                    payload={
                        "agent_id": point["agent_id"],
                        "item_id": point["item_id"],
                        "text": point["text"],
                        "session_id": point["session_id"],
                        "occurred_at_ms": point["occurred_at_ms"],
                        "project_id": point.get("project_id", ""),
                    },
                )
            ],
        )

    def search(self, agent_id: str, vector: list[float], limit: int, project_id: str = "") -> list[dict]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        must = [FieldCondition(key="agent_id", match=MatchValue(value=agent_id))]
        if project_id:
            must.append(FieldCondition(key="project_id", match=MatchValue(value=project_id)))
        found = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            query_filter=Filter(must=must),
            limit=limit,
        )
        out = []
        for hit in found.points:
            payload = hit.payload or {}
            if payload.get("agent_id") != agent_id:
                continue
            if project_id and payload.get("project_id") != project_id:
                continue
            out.append(
                {
                    "agent_id": agent_id,
                    "item_id": payload.get("item_id") or "",
                    "text": payload.get("text") or "",
                    "score": float(hit.score or 0.0),
                    "session_id": payload.get("session_id") or "",
                    "occurred_at_ms": int(payload.get("occurred_at_ms") or 0),
                    "project_id": payload.get("project_id") or "",
                }
            )
        return out


class MiniLM:
    def __init__(self, name: str) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(name)
        self.dims = int(self._model.get_sentence_embedding_dimension())

    def embed(self, text: str) -> list[float]:
        vec = self._model.encode(text or " ", normalize_embeddings=True)
        return [float(x) for x in vec]


class AgentBackend:
    name = "agent"
    degraded = False

    def __init__(self, cfg: MemoryConfig, rows=None, vectors=None, embed=None) -> None:
        self._cfg = cfg
        self._rows = rows
        self._vectors = vectors
        self._embed = embed
        self._lock = asyncio.Lock()
        self._ready = rows is not None and vectors is not None and embed is not None

    def open(self) -> None:
        if self._ready:
            return
        embed = MiniLM(self._cfg.embed_model)
        self._embed = embed
        self._rows = PostgresRows(self._cfg.postgres_dsn)
        self._vectors = QdrantVectors(
            self._cfg.qdrant_host,
            self._cfg.qdrant_port,
            f"{self._cfg.qdrant_collection}_agent_{embed.dims}",
            embed.dims,
        )
        self._ready = True

    async def warmup(self) -> list[int]:
        async def embed(text: str) -> None:
            async with self._lock:
                await asyncio.to_thread(self._embed.embed, text)

        async def once() -> int:
            resp = await self.recall(
                memory_pb2.RecallRequest(agent_id="_warmup", query_text="hello", top_k=1)
            )
            return resp.meta.latency_ms

        return await time_warm_queries(embed, once)

    async def recall(self, req: memory_pb2.RecallRequest) -> memory_pb2.RecallResponse:
        started = time.time()
        if not req.agent_id:
            return assemble_response(req, [], started=started)
        async with self._lock:
            items = await asyncio.to_thread(self._recall_sync, req)
        return assemble_response(req, items, started=started)

    def _recall_sync(self, req: memory_pb2.RecallRequest) -> list[memory_pb2.MemoryItem]:
        project_id = (req.project_id or "").strip()
        rows = self._rows.list_agent(req.agent_id)
        # role/rule/principle 是 agent 级稳定记忆，不按 project 过滤。
        items = [_item(row) for row in rows if row["kind"] in _STABLE]
        query = req.query_text.strip()
        limit = req.top_k or 5
        if query:
            vector = self._embed.embed(query)
            hits = self._vectors.search(req.agent_id, vector, limit, project_id)
        else:
            hits = [
                row
                for row in rows
                if row["kind"] == memory_pb2.MEMORY_KIND_EPISODE
                and (not project_id or str(row.get("project_id") or "") == project_id)
            ]
        seen = {item.item_id for item in items}
        for hit in hits:
            if hit.get("agent_id") != req.agent_id:
                continue
            if project_id and str(hit.get("project_id") or "") != project_id:
                continue
            item = _item(hit, kind=memory_pb2.MEMORY_KIND_EPISODE)
            if not item.text or item.item_id in seen:
                continue
            items.append(item)
            seen.add(item.item_id)
        return items

    async def write_episode(self, req: memory_pb2.WriteEpisodeRequest) -> str:
        episode = req.episode
        text = episode.summary.strip()
        if not text or not episode.agent_id:
            return ""
        item_id = f"ep_{episode.agent_id}_{episode.session_id or 'none'}"
        row = {
            "item_id": item_id,
            "agent_id": episode.agent_id,
            "kind": memory_pb2.MEMORY_KIND_EPISODE,
            "text": text,
            "score": 0.0,
            "source_session_id": episode.session_id,
            "occurred_at_ms": episode.occurred_at_ms,
            "session_id": episode.session_id,
            "project_id": episode.project_id,
        }
        async with self._lock:
            await asyncio.to_thread(self._write_sync, row)
        return item_id

    def _write_sync(self, row: dict) -> None:
        self._rows.upsert(row)
        vector = self._embed.embed(row["text"])
        point = dict(row)
        point["vector"] = vector
        self._vectors.upsert(point)

    async def write_document(self, req: memory_pb2.WriteDocumentRequest) -> str:
        del req
        return ""

    async def graph_view(self, agent_id: str) -> dict:
        async with self._lock:
            rows = await asyncio.to_thread(self._rows.list_agent, agent_id)
        nodes = [{"id": agent_id, "name": agent_id, "summary": "This agent's memory."}]
        edges = []
        for row in rows:
            if int(row.get("kind") or 0) != memory_pb2.MEMORY_KIND_EPISODE:
                continue
            item_id = str(row.get("item_id") or "")
            text = str(row.get("text") or "").strip()
            if not item_id or not text:
                continue
            nodes.append({"id": item_id, "name": "episode", "summary": text[:500]})
            edges.append(
                {
                    "id": item_id,
                    "source": agent_id,
                    "target": item_id,
                    "fact": text[:240],
                    "name": "REMEMBERS",
                }
            )
        return build_graph_view(self.name, agent_id, nodes, edges)


def _item(row: dict, kind: int | None = None) -> memory_pb2.MemoryItem:
    return memory_pb2.MemoryItem(
        item_id=str(row.get("item_id") or ""),
        agent_id=str(row.get("agent_id") or ""),
        kind=int(kind if kind is not None else row.get("kind") or 0),
        text=str(row.get("text") or ""),
        score=float(row.get("score") or 0.0),
        occurred_at_ms=int(row.get("occurred_at_ms") or 0),
        source_session_id=str(row.get("session_id") or row.get("source_session_id") or ""),
        project_id=str(row.get("project_id") or ""),
    )
