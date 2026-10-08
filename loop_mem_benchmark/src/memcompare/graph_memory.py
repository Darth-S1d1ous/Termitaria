"""Entity-relation memory stored in embedded Kuzu and retrieved by traversal.

Retrieval does not use vector similarity. A query is mapped to entity nodes
with the same offline extractor plus exact phrase lookup against entity names
already in the graph. Those seeds are expanded one relational hop in Cypher.
Utterances are ranked by the sum of inverse-degree weights of the entities
that connect them to the query (direct mentions weigh more than neighbors).
High-degree hubs are not expanded, so a speaker node cannot pull in every
turn that mentions that person through a one-hop flood.
"""

from __future__ import annotations

import math
from typing import Any

import kuzu

from memcompare.extractor import Extractor, Extraction
from memcompare.memory import Memory, MemoryHit
from memcompare.textnorm import STOP_NORMS, ngrams

_DIRECT = 1.0
_HOP = 0.35
_EXPAND_MAX_DEGREE = 40


class GraphMemory(Memory):
    name = "graph_kuzu"

    def __init__(self, extractor: Extractor | None = None) -> None:
        self.extractor = extractor or Extractor()
        self._buffer: list[tuple[str, str, str | None]] = []
        self._known_speakers: set[str] = set()
        self._degrees: dict[str, int] = {}
        self._entity_norms: set[str] = set()
        self._dirty_index = True
        self._stats = {"utterances": 0, "entities": 0, "mentions": 0, "relations": 0}
        self._db: kuzu.Database | None = None
        self._conn: kuzu.Connection | None = None
        self._open()

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        meta = metadata or {}
        chunk_id = str(meta.get("chunk_id") or f"c{self._stats['utterances'] + len(self._buffer)}")
        speaker = meta.get("speaker")
        speaker_s = str(speaker) if speaker else None
        if speaker_s:
            self._known_speakers.add(speaker_s)
        self._buffer.append((text, chunk_id, speaker_s))
        self._dirty_index = True

    def retrieve(self, query: str, k: int = 5) -> list[MemoryHit]:
        self._flush()
        if k <= 0 or self._stats["utterances"] == 0:
            return []
        seeds = self._seeds(query)
        if not seeds:
            return []
        direct = self._rows(
            """
            MATCH (e:Entity)
            WHERE e.norm IN $seeds
            MATCH (u:Utterance)-[:MENTIONS]->(e)
            RETURN u.dia_id, u.text, u.seq, e.norm
            """,
            {"seeds": seeds},
        )
        expandable = [s for s in seeds if 0 < self._degrees.get(s, 0) <= _EXPAND_MAX_DEGREE]
        hop_rows: list[list] = []
        if expandable:
            hop_rows = self._rows(
                """
                MATCH (e:Entity)
                WHERE e.norm IN $seeds
                MATCH (e)-[:RELATES]-(n:Entity)
                WHERE n.mention_count > 0
                  AND n.mention_count <= $max_deg
                  AND NOT n.norm IN $all_seeds
                MATCH (u:Utterance)-[:MENTIONS]->(n)
                RETURN u.dia_id, u.text, u.seq, n.norm
                """,
                {"seeds": expandable, "all_seeds": seeds, "max_deg": _EXPAND_MAX_DEGREE},
            )
        return _rank(direct, hop_rows, self._degrees, k)

    def clear(self) -> None:
        self._buffer.clear()
        self._known_speakers.clear()
        self._degrees.clear()
        self._entity_norms.clear()
        self._dirty_index = True
        self._stats = {"utterances": 0, "entities": 0, "mentions": 0, "relations": 0}
        self._close()
        self._open()

    @property
    def stats(self) -> dict[str, int]:
        self._flush()
        return dict(self._stats)

    def _seeds(self, query: str) -> list[str]:
        extracted = self.extractor.extract_many([query], [None], self._known_speakers)[0]
        seeds = {ent.norm for ent in extracted.entities if ent.norm not in STOP_NORMS}
        if self._entity_norms:
            seeds |= ngrams(query) & self._entity_norms
        seeds = {s for s in seeds if s and s not in STOP_NORMS and s in self._entity_norms}
        return sorted(seeds)

    def _flush(self) -> None:
        if not self._buffer:
            return
        texts = [t for t, _, _ in self._buffer]
        speakers = [s for _, _, s in self._buffer]
        extractions = self.extractor.extract_many(texts, speakers, self._known_speakers)
        conn = self._conn
        assert conn is not None
        conn.execute("BEGIN TRANSACTION")
        try:
            for (text, chunk_id, _speaker), extraction in zip(self._buffer, extractions, strict=True):
                self._write(conn, text, chunk_id, extraction)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        self._buffer.clear()
        self._refresh_index()

    def _write(self, conn: kuzu.Connection, text: str, chunk_id: str, extraction: Extraction) -> None:
        seq = self._stats["utterances"]
        conn.execute(
            "CREATE (u:Utterance {dia_id: $id, text: $text, seq: $seq})",
            {"id": chunk_id, "text": text, "seq": seq},
        )
        self._stats["utterances"] += 1
        for ent in extraction.entities:
            conn.execute(
                """
                MERGE (e:Entity {norm: $norm})
                ON CREATE SET e.name = $name, e.type = $type, e.mention_count = 0
                """,
                {"norm": ent.norm, "name": ent.name, "type": ent.type},
            )
            conn.execute(
                """
                MATCH (u:Utterance {dia_id: $id}), (e:Entity {norm: $norm})
                MERGE (u)-[:MENTIONS]->(e)
                """,
                {"id": chunk_id, "norm": ent.norm},
            )
            self._stats["mentions"] += 1
        for rel in extraction.relations:
            conn.execute(
                """
                MATCH (a:Entity {norm: $s}), (b:Entity {norm: $o})
                MERGE (a)-[:RELATES {predicate: $p}]->(b)
                """,
                {"s": rel.subject, "o": rel.object, "p": rel.predicate},
            )
            self._stats["relations"] += 1

    def _refresh_index(self) -> None:
        conn = self._conn
        assert conn is not None
        conn.execute(
            """
            MATCH (e:Entity)
            OPTIONAL MATCH (e)<-[:MENTIONS]-(u:Utterance)
            WITH e, count(u) AS c
            SET e.mention_count = c
            """
        )
        self._degrees.clear()
        self._entity_norms.clear()
        result = conn.execute("MATCH (e:Entity) RETURN e.norm, e.mention_count")
        while result.has_next():
            norm, count = result.get_next()
            self._degrees[norm] = int(count)
            self._entity_norms.add(norm)
        self._stats["entities"] = len(self._entity_norms)
        self._stats["mentions"] = self._scalar("MATCH ()-[r:MENTIONS]->() RETURN count(*)")
        self._stats["relations"] = self._scalar("MATCH ()-[r:RELATES]->() RETURN count(*)")
        self._dirty_index = False

    def _scalar(self, query: str) -> int:
        conn = self._conn
        assert conn is not None
        result = conn.execute(query)
        if not result.has_next():
            return 0
        value = result.get_next()[0]
        return int(value)

    def _rows(self, query: str, params: dict) -> list[list]:
        conn = self._conn
        assert conn is not None
        result = conn.execute(query, params)
        rows = []
        while result.has_next():
            rows.append(result.get_next())
        return rows

    def _open(self) -> None:
        self._db = kuzu.Database(":memory:")
        self._conn = kuzu.Connection(self._db)
        self._conn.execute(
            """
            CREATE NODE TABLE Entity(
                norm STRING,
                name STRING,
                type STRING,
                mention_count INT64,
                PRIMARY KEY (norm)
            )
            """
        )
        self._conn.execute(
            """
            CREATE NODE TABLE Utterance(
                dia_id STRING,
                text STRING,
                seq INT64,
                PRIMARY KEY (dia_id)
            )
            """
        )
        self._conn.execute("CREATE REL TABLE MENTIONS(FROM Utterance TO Entity)")
        self._conn.execute("CREATE REL TABLE RELATES(FROM Entity TO Entity, predicate STRING)")

    def _close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
        if self._db is not None:
            self._db.close()
            self._db = None


def _idf(degree: int) -> float:
    return 1.0 / math.log(2.0 + max(degree, 0))


def _rank(direct: list[list], hop_rows: list[list], degrees: dict[str, int], k: int) -> list[MemoryHit]:
    score: dict[str, float] = {}
    texts: dict[str, str] = {}
    seqs: dict[str, int] = {}
    direct_ents: dict[str, set[str]] = {}
    hop_ents: dict[str, set[str]] = {}

    seen_direct: set[tuple[str, str]] = set()
    for dia, text, seq, norm in direct:
        if (dia, norm) in seen_direct:
            continue
        seen_direct.add((dia, norm))
        score[dia] = score.get(dia, 0.0) + _DIRECT * _idf(degrees.get(norm, 1))
        texts[dia] = text
        seqs[dia] = int(seq)
        direct_ents.setdefault(dia, set()).add(norm)

    for dia, text, seq, norm in hop_rows:
        if norm in direct_ents.get(dia, ()):
            continue
        bucket = hop_ents.setdefault(dia, set())
        if norm in bucket:
            continue
        bucket.add(norm)
        score[dia] = score.get(dia, 0.0) + _HOP * _idf(degrees.get(norm, 1))
        texts[dia] = text
        seqs[dia] = int(seq)

    ordered = sorted(score.items(), key=lambda item: (-item[1], seqs[item[0]], item[0]))
    hits: list[MemoryHit] = []
    for dia, sc in ordered[:k]:
        hits.append(
            MemoryHit(
                chunk_id=dia,
                text=texts[dia],
                score=sc,
                detail={
                    "direct_entities": sorted(direct_ents.get(dia, ())),
                    "hop_entities": sorted(hop_ents.get(dia, ())),
                },
            )
        )
    return hits

