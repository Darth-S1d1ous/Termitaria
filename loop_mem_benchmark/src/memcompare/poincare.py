"""Poincaré ball projection and distance.

The distance is the open-unit-ball hyperbolic distance

    d(u, v) = arcosh(1 + 2 * ||u - v||^2 / ((1 - ||u||^2) * (1 - ||v||^2)))

Encoder outputs are mean-pooled MiniLM states *before* the model's final L2
normalization (that layer forces every vector onto the unit sphere and would
make this distance a monotone function of cosine). They are then sent into
the ball by the origin exponential map

    exp_0(x) = tanh(||s x||) * (s x) / ||s x||

with a fixed scale ``s = 0.2``. MiniLM mean-pool norms on dialogue turns are
typically about 2–7, so this scale lands radii around 0.4–0.9. The scale is
not fit to labels. Projected points are clipped to norm <= 1 - 1e-5.
"""

from __future__ import annotations

import numpy as np

PROJECTION_SCALE = 0.2
_NORM_EPS = 1e-5
_DIST_EPS = 1e-7


def project_to_ball(x: np.ndarray, scale: float = PROJECTION_SCALE) -> np.ndarray:
    """Map Euclidean vectors into the open unit ball. Supports shape (d,) or (n, d)."""

    v = np.asarray(x, dtype=np.float64) * float(scale)
    single = v.ndim == 1
    if single:
        v = v.reshape(1, -1)
    norm = np.linalg.norm(v, axis=-1, keepdims=True)
    safe = np.maximum(norm, 1e-12)
    out = np.tanh(norm) * (v / safe)
    out = np.where(norm < 1e-12, 0.0, out)
    n = np.linalg.norm(out, axis=-1, keepdims=True)
    cap = 1.0 - _NORM_EPS
    out = out * np.minimum(1.0, cap / np.maximum(n, 1e-12))
    if single:
        return out[0]
    return out


def poincare_distance(u: np.ndarray, v: np.ndarray) -> float:
    """Distance between two points already inside the open unit ball."""

    return float(poincare_distances(u, np.asarray(v, dtype=np.float64).reshape(1, -1))[0])


def poincare_distances(query: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Distance from one point to each row of ``points``. Both must have norm < 1."""

    q = np.asarray(query, dtype=np.float64).reshape(-1)
    pts = np.asarray(points, dtype=np.float64)
    if pts.size == 0:
        return np.zeros((0,), dtype=np.float64)
    diff = pts - q
    diff_sq = np.sum(diff * diff, axis=1)
    qn = float(np.sum(q * q))
    pn = np.sum(pts * pts, axis=1)
    denom = np.maximum((1.0 - qn) * (1.0 - pn), _DIST_EPS)
    arg = np.maximum(1.0 + 2.0 * diff_sq / denom, 1.0)
    # np.arccosh is unstable very close to 1; the identity below matches it.
    return np.arccosh(arg)
