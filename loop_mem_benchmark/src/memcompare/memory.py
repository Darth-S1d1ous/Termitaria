"""Swappable memory interface used by the ReAct loop."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class MemoryHit:
    """One retrieved memory chunk. Higher ``score`` is better."""

    chunk_id: str
    text: str
    score: float
    detail: dict[str, Any] = field(default_factory=dict)


class Memory(ABC):
    """add / retrieve / clear. Implementations must not share stored state."""

    name: str

    @abstractmethod
    def add(self, text: str, metadata: dict[str, Any] | None = None) -> None:
        """Store one text chunk. ``metadata['chunk_id']`` is the stable id."""

    @abstractmethod
    def retrieve(self, query: str, k: int = 5) -> list[MemoryHit]:
        """Return up to ``k`` chunks, best first."""

    @abstractmethod
    def clear(self) -> None:
        """Drop every stored chunk."""
