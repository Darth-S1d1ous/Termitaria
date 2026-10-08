"""Minimal memory: ingest text, embed, project into the Poincaré ball, rank by distance."""

from __future__ import annotations

from typing import Any

import numpy as np

from memcompare.memory import Memory, MemoryHit
from memcompare.poincare import PROJECTION_SCALE, poincare_distances, project_to_ball

_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class MiniLMEncoder:
    """Mean-pooled all-MiniLM-L6-v2, before the unit-length normalize layer."""

    def __init__(self, model_name: str = _MODEL) -> None:
        import torch
        from sentence_transformers import SentenceTransformer

        self.torch = torch
        self.model = SentenceTransformer(model_name, device="cpu")
        self.model.eval()
        self.model_name = model_name

    def encode(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 384), dtype=np.float64)
        outs: list[np.ndarray] = []
        batch = 64
        torch = self.torch
        for start in range(0, len(texts), batch):
            chunk = texts[start : start + batch]
            features = self.model.tokenizer(
                chunk,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            with torch.no_grad():
                token_out = self.model[0](features)
                pooled = self.model[1](token_out)["sentence_embedding"]
            outs.append(pooled.detach().cpu().numpy())
        return np.vstack(outs).astype(np.float64, copy=False)


class PoincareMemory(Memory):
    name = "poincare"

    def __init__(self, encoder: MiniLMEncoder | Any | None = None, scale: float = PROJECTION_SCALE) -> None:
        self.encoder = encoder if encoder is not None else MiniLMEncoder()
        self.scale = scale
        self._ids: list[str] = []
        self._texts: list[str] = []
        self._raw: list[np.ndarray] = []
        self._points: np.ndarray | None = None
        self._pending: list[tuple[str, str]] = []
        self._last_raw_query: np.ndarray | None = None
        self._stashed_norms: list[np.ndarray] = []

    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        meta = metadata or {}
        chunk_id = str(meta.get("chunk_id") or f"c{len(self._ids) + len(self._pending)}")
        self._pending.append((chunk_id, text))
        self._points = None

    def retrieve(self, query: str, k: int = 5) -> list[MemoryHit]:
        self._flush()
        if k <= 0 or self._points is None or len(self._ids) == 0:
            return []
        raw = np.asarray(self.encoder.encode([query])[0], dtype=np.float64)
        self._last_raw_query = raw
        projected = project_to_ball(raw, self.scale)
        dists = poincare_distances(projected, self._points)
        order = np.lexsort((np.arange(len(self._ids)), dists))
        hits: list[MemoryHit] = []
        for idx in order[:k]:
            dist = float(dists[idx])
            hits.append(
                MemoryHit(
                    chunk_id=self._ids[idx],
                    text=self._texts[idx],
                    score=-dist,
                    detail={"distance": dist, "radius": float(np.linalg.norm(self._points[idx]))},
                )
            )
        return hits

    def cosine_ids(self, k: int = 5) -> list[str]:
        """Top-k by cosine on the same pre-normalization vectors. Diagnostic only."""

        self._flush()
        if self._last_raw_query is None or not self._raw:
            return []
        mat = np.vstack(self._raw)
        q = self._last_raw_query
        qn = np.linalg.norm(q) + 1e-12
        pn = np.linalg.norm(mat, axis=1) + 1e-12
        sims = (mat @ q) / (pn * qn)
        order = np.lexsort((np.arange(len(self._ids)), -sims))
        return [self._ids[int(i)] for i in order[:k]]

    def clear(self) -> None:
        self._stash_norms()
        self._ids.clear()
        self._texts.clear()
        self._raw.clear()
        self._pending.clear()
        self._points = None
        self._last_raw_query = None

    def radius_stats(self) -> dict[str, float]:
        self._flush()
        parts = list(self._stashed_norms)
        if self._points is not None and len(self._points):
            parts.append(np.linalg.norm(self._points, axis=1))
        if not parts:
            return {"min": 0.0, "median": 0.0, "max": 0.0}
        norms = np.concatenate(parts)
        return {
            "min": float(norms.min()),
            "median": float(np.median(norms)),
            "max": float(norms.max()),
        }

    def _stash_norms(self) -> None:
        self._flush()
        if self._points is not None and len(self._points):
            self._stashed_norms.append(np.linalg.norm(self._points, axis=1).copy())

    def _flush(self) -> None:
        if not self._pending:
            if self._points is None and self._raw:
                self._points = project_to_ball(np.vstack(self._raw), self.scale)
            return
        vectors = self.encoder.encode([text for _, text in self._pending])
        for (chunk_id, text), vector in zip(self._pending, vectors, strict=True):
            self._ids.append(chunk_id)
            self._texts.append(text)
            self._raw.append(np.asarray(vector, dtype=np.float64))
        self._pending.clear()
        self._points = project_to_ball(np.vstack(self._raw), self.scale)
