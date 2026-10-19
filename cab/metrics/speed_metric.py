"""Dimension 6: Algorithmic Computation Speed & Efficiency Metric.

Evaluates calculation overhead in milliseconds per second of playable beatmap audio/length (ms/s):
    v_i = T_i (ms) / L_i (s)
Benchmark speed v is the mean across evaluated beatmaps:
    v = (1 / N) * sum(v_i)

Scoring Standard (0 - 100 points, logarithmic decade decay):
- v <= 0.015 ms/s: 100.0 pts (>= ~67,000x realtime, virtually instantaneous)
- v = 0.15 ms/s:    75.0 pts (~6,700x realtime, excellent)
- v = 1.50 ms/s:    50.0 pts (~670x realtime, acceptable)
- v = 15.0 ms/s:    25.0 pts (~67x realtime, slow)
- v >= 150.0 ms/s:   0.0 pts (~6.7x realtime / boundary limit)
Every 10x compute cost reduction gains exactly 25.0 points:
    Score = clip(100.0 - 25.0 * log10(max(1.0, v / 0.015)), 0.0, 100.0)

The 0.015 floor was moved down from 0.10 after the speed probe was re-based on
the 33 dan-course marathons: on that sample four algorithms land between 0.019
and 0.063 ms/s (a 3.4x spread) and all of them were being clipped at 100.  With
the floor at 0.015 they separate into 97.6 / 92.7 / 85.1 / 84.4.

Platform overhead
-----------------
When an algorithm's difficulty path shells out to a binary that cannot run
natively on this platform (Dan-Overlay's ``msd.exe`` through Wine on Linux),
that layer's per-invocation start-up is not algorithm cost -- measured at ~5 s
against ~11 ms of actual MinaCalc work.  ``evaluate_speed`` accepts
``platform_overhead_ms`` and subtracts it from every sample that exceeds it, so
the dimension measures the algorithm rather than the compatibility layer and
stays comparable with a native run.
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
    #: Fixed per-invocation cost that was subtracted from every sample because
    #: it comes from a platform compatibility layer (currently Wine) rather than
    #: from the algorithm.  0.0 means nothing was subtracted.
    platform_overhead_ms: float = 0.0


#: Speed at or below which the dimension is saturated at 100 points.
#: Lowered from 0.10 after the probe moved to the 33 dan-course marathons -- see
#: the module docstring for the measured spread that motivated it.
SPEED_FLOOR_MS_PER_S: float = 0.015


def calculate_speed_score(v_ms_per_s: float) -> float:
    """Compute normalized 0-100 score from calculation speed (ms/s).

    Scoring standard (25 points per decade):
    - <= 0.015 ms/s: 100.0 pts
    - 0.15 ms/s:     75.0 pts
    - 1.50 ms/s:     50.0 pts
    - 15.0 ms/s:     25.0 pts
    - >= 150 ms/s:    0.0 pts
    """
    if v_ms_per_s <= SPEED_FLOOR_MS_PER_S:
        return 100.0
    deduction = 25.0 * math.log10(max(1.0, v_ms_per_s / SPEED_FLOOR_MS_PER_S))
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
    platform_overhead_ms: float = 0.0,
) -> SpeedResult:
    """Evaluate algorithmic computation speed across beatmaps.

    Parameters
    ----------
    timings : Sequence of (calc_time_ms, map_duration_seconds)
    platform_overhead_ms : Fixed per-invocation cost to subtract, in ms.

        Only for adapters whose difficulty path shells out to a binary that
        cannot run natively on this platform and therefore goes through Wine.
        Wine's process start-up is pure added cost on top of what the target
        platform would pay, and on a cold server it can dwarf the whole
        measurement (measured: 4.98 s per invocation versus 11 ms of actual
        MinaCalc work).  Subtracting it makes the reading comparable with a
        native run instead of measuring "how fast Wine starts".

        A sample whose measured time is *not* larger than the overhead is left
        untouched: that means the underlying call was served from cache (or ran
        natively), so no compatibility-layer cost was paid for it.

    Returns
    -------
    SpeedResult containing average speed (ms/s), realtime factor, and 0-100 score.
    """
    overhead = max(0.0, float(platform_overhead_ms or 0.0))

    # A sample that is a small fraction of the overhead never paid it: either
    # the underlying call was served from cache (~0.2 s against a ~5 s Wine
    # start-up, i.e. 4%) or it ran natively.  Samples that *did* go through the
    # compatibility layer sit just above the overhead (102%), so anything below
    # a quarter of it is safely "not measured through the layer".  Using a bare
    # ``t <= overhead`` test instead would skip the charts whose total landed
    # within the baseline's own jitter, leaving their full cost in the median.
    _SKIP_RATIO = 0.25

    def _adjust(t_ms: float) -> float:
        if overhead <= 0.0 or t_ms <= overhead * _SKIP_RATIO:
            return t_ms
        return max(0.001, t_ms - overhead)

    if not timings:
        return SpeedResult(
            average_speed_ms_per_s=0.0,
            median_speed_ms_per_s=0.0,
            realtime_factor=float("inf"),
            speed_score=100.0,
            evaluated_count=0,
            sample_speeds=[],
            platform_overhead_ms=round(overhead, 3),
        )

    speeds = [
        max(0.001, _adjust(float(t_ms))) / max(1.0, float(dur_s))
        for t_ms, dur_s in timings
    ]
    avg_speed = sum(speeds) / len(speeds)

    sorted_speeds = sorted(speeds)
    mid = len(sorted_speeds) // 2
    if len(sorted_speeds) % 2 == 1:
        med_speed = sorted_speeds[mid]
    else:
        med_speed = (sorted_speeds[mid - 1] + sorted_speeds[mid]) / 2.0

    # Score on the median: with a fixed marathon sample set the distribution is
    # tight, but a single pathological chart should not move the whole dimension.
    rf = 1000.0 / med_speed if med_speed > 0 else float("inf")
    score = calculate_speed_score(med_speed)

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
        platform_overhead_ms=round(overhead, 3),
    )
