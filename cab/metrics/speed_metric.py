"""Dimension 6: Algorithmic Computation Speed & Efficiency Metric.

Evaluates calculation overhead in milliseconds per second of playable beatmap audio/length (ms/s):
    v_i = T_i (ms) / L_i (s)
Benchmark speed v is the mean across evaluated beatmaps:
    v = (1 / N) * sum(v_i)

Scoring Standard (0 - 100 points, logarithmic decade decay):
- v <= 0.10 ms/s: 100.0 pts (>= 10,000x realtime, virtually instantaneous)
- v = 1.00 ms/s:   75.0 pts (1,000x realtime, excellent)
- v = 10.00 ms/s:  50.0 pts (100x realtime, acceptable)
- v = 100.00 ms/s: 25.0 pts (10x realtime, slow)
- v >= 1000.0 ms/s: 0.0 pts (1x realtime / boundary limit)
Every 10x compute cost reduction gains exactly 25.0 points:
    Score = clip(100.0 - 25.0 * log10(max(1.0, v / 0.10)), 0.0, 100.0)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Sequence, Tuple


@dataclass(slots=True)
class SpeedResult:
    """Benchmark result for algorithmic computation speed."""

    average_speed_ms_per_s: float
    median_speed_ms_per_s: float
    realtime_factor: float
    speed_score: float
    evaluated_count: int
    sample_speeds: List[float] = field(default_factory=list)
    p95_speed_ms_per_s: float = 0.0
    max_speed_ms_per_s: float = 0.0


def calculate_speed_score(v_ms_per_s: float) -> float:
    """Compute normalized 0-100 score from average calculation speed (ms/s).

    Scoring standard:
    - <= 0.10 ms/s: 100.0 pts
    - 1.00 ms/s: 75.0 pts
    - 10.00 ms/s: 50.0 pts
    - 100.00 ms/s: 25.0 pts
    - >= 1000.00 ms/s: 0.0 pts
    """
    if v_ms_per_s <= 0.10:
        return 100.0
    deduction = 25.0 * math.log10(max(1.0, v_ms_per_s / 0.10))
    return round(max(0.0, min(100.0, 100.0 - deduction)), 2)


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (numpy 'linear' method compatible)."""
    n = len(sorted_vals)
    if n == 0:
        return 0.0
    if n == 1:
        return float(sorted_vals[0])
    pos = (n - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(sorted_vals[lo])
    return float(sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo))


def evaluate_speed(
    timings: Sequence[Tuple[float, float]],
) -> SpeedResult:
    """Evaluate algorithmic computation speed across beatmaps.

    Parameters
    ----------
    timings : Sequence of (calc_time_ms, map_duration_seconds)

    Returns
    -------
    SpeedResult containing average speed (ms/s), realtime factor, and 0-100 score.
    """
    if not timings:
        return SpeedResult(
            average_speed_ms_per_s=0.0,
            median_speed_ms_per_s=0.0,
            realtime_factor=float("inf"),
            speed_score=100.0,
            evaluated_count=0,
            sample_speeds=[],
        )

    speeds = [
        max(0.001, float(t_ms)) / max(1.0, float(dur_s))
        for t_ms, dur_s in timings
    ]
    avg_speed = sum(speeds) / len(speeds)

    sorted_speeds = sorted(speeds)
    mid = len(sorted_speeds) // 2
    if len(sorted_speeds) % 2 == 1:
        med_speed = sorted_speeds[mid]
    else:
        med_speed = (sorted_speeds[mid - 1] + sorted_speeds[mid]) / 2.0

    rf = 1000.0 / avg_speed if avg_speed > 0 else float("inf")
    score = calculate_speed_score(avg_speed)

    p95_speed = _percentile(sorted_speeds, 0.95)
    max_speed = sorted_speeds[-1]

    return SpeedResult(
        average_speed_ms_per_s=round(avg_speed, 4),
        median_speed_ms_per_s=round(med_speed, 4),
        realtime_factor=round(rf, 1),
        speed_score=score,
        evaluated_count=len(speeds),
        sample_speeds=[round(s, 4) for s in speeds],
        p95_speed_ms_per_s=round(p95_speed, 4),
        max_speed_ms_per_s=round(max_speed, 4),
    )
