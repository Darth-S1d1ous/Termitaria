import math

import numpy as np

from memcompare.poincare import poincare_distance, poincare_distances, project_to_ball
from memcompare.poincare_memory import PoincareMemory


def test_distance_matches_the_ball_formula():
    u = np.array([0.1, -0.2])
    v = np.array([0.3, 0.05])
    diff_sq = float(np.sum((u - v) ** 2))
    denom = (1 - float(np.sum(u**2))) * (1 - float(np.sum(v**2)))
    expected = math.acosh(1 + 2 * diff_sq / denom)
    assert math.isclose(poincare_distance(u, v), expected, rel_tol=1e-12, abs_tol=1e-12)
    assert poincare_distance(u, v) == poincare_distance(v, u)
    assert poincare_distance(u, u) == 0.0


def test_projection_stays_inside_the_open_ball():
    raw = np.array([[100.0, -40.0, 5.0], [0.0, 0.0, 0.0], [0.2, 0.1, -0.3]])
    projected = project_to_ball(raw, scale=0.2)
    norms = np.linalg.norm(projected, axis=1)
    assert np.all(norms < 1.0)
    assert norms[1] == 0.0


def test_radius_can_change_rank_relative_to_cosine():
    # Same direction at a very different radius vs a small angle at the query radius.
    query = np.array([0.30, 0.0])
    far_same_direction = np.array([0.90, 0.0])
    nearby_angle = np.array([0.30 * math.cos(0.25), 0.30 * math.sin(0.25)])
    points = np.vstack([far_same_direction, nearby_angle])
    dists = poincare_distances(query, points)
    cosine = []
    for point in points:
        cosine.append(float(np.dot(query, point) / (np.linalg.norm(query) * np.linalg.norm(point))))
    assert int(np.argmin(dists)) != int(np.argmax(cosine))
    assert int(np.argmin(dists)) == 1
    assert int(np.argmax(cosine)) == 0


class _FakeEncoder:
    def __init__(self, table: dict[str, np.ndarray]) -> None:
        self.table = table

    def encode(self, texts: list[str]) -> np.ndarray:
        return np.vstack([self.table[text] for text in texts])


def test_memory_ranks_by_poincare_distance_and_clears():
    direction = np.array([1.0, 0.0, 0.0])
    other = np.array([0.0, 1.0, 0.0])
    table = {
        "close": direction * 4.0,
        "far": other * 4.0,
        "query": direction * 4.2,
    }
    memory = PoincareMemory(encoder=_FakeEncoder(table), scale=0.2)
    memory.add("close", {"chunk_id": "c1"})
    memory.add("far", {"chunk_id": "c2"})
    hits = memory.retrieve("query", k=2)
    assert [hit.chunk_id for hit in hits] == ["c1", "c2"]
    assert hits[0].detail["distance"] < hits[1].detail["distance"]
    assert hits[0].score == -hits[0].detail["distance"]
    memory.clear()
    assert memory.retrieve("query", k=2) == []
