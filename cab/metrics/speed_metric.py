"""Dimension 6: Algorithmic Computation Speed & Efficiency Metric.

Evaluates calculation overhead in milliseconds per second of playable beatmap
audio/length (ms/s)::

    v_i = T_i (ms) / L_i (s)

Absolute scoring standard (0 - 100 points, logarithmic decade decay)::

    Score = clip(100.0 - 25.0 * log10(max(1.0, v / 0.015)), 0.0, 100.0)

Cross-machine scoring uses the Hill curve on ``rho = v_algo / v_baseline``::

    score = 100 / (1 + (rho / 386.25) ** 0.2886)

Data policy:

* A non-finite timing or difficulty is a **measurement failure**, never a fast
  run: it is dropped, and if nothing remains ``speed_score`` is ``None``.
* ``realtime_factor`` is ``None`` (not ``inf``) when there is no data, so the
  JSON artefact stays valid for strict parsers.
* A sample that is not larger than the platform overhead is left untouched;
  the old 25%-of-overhead shortcut turned cached calls into "instant" ones.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple


@dataclass(slots=True)
class SpeedResult:
    """Benchmark result for algorithmic computation speed."""

    average_speed_ms_per_s: float
    median_speed_ms_per_s: float
    #: ``None`` when nothing was measured (avoids ``Infinity`` in JSON).
    realtime_factor: Optional[float]
    #: ``None`` when nothing was measured -> dimension renders as "—".
    speed_score: Optional[float]
    evaluated_count: int
    sample_speeds: List[float] = field(default_factory=list)
    p95_speed_ms_per_s: float = 0.0
    max_speed_ms_per_s: float = 0.0
    #: Fixed per-invocation cost that was subtracted from every sample because
    #: it comes from a platform compatibility layer (currently Wine) rather than
    #: from the algorithm.  0.0 means nothing was subtracted.
    platform_overhead_ms: float = 0.0
    #: Cross-machine reading: ``v_algo / v_baseline`` (MinaCalc on the same
    #: probe, same machine, moments apart).  1.0 == baseline speed.
    baseline_ratio: Optional[float] = None
    #: Median speed of the baseline algorithm on the same probe.
    baseline_ms_per_s: Optional[float] = None
    #: Name of the baseline algorithm (e.g. "MinaCalc-0.72.3").
    baseline_algorithm: str = ""
    #: Timings rejected because they were non-finite or carried no duration.
    rejected_samples: int = 0


#: Speed at or below which the dimension is saturated at 100 points.
SPEED_FLOOR_MS_PER_S: float = 0.015

#: Hill-curve anchor: the relative speed (v_algo / v_baseline) that scores
#: 50 points.  Calibrated on the reference machine (MinaCalc = 0.0211 ms/s) so
#: that 0.02 ms/s -> 85 points and 2 ms/s -> 60 points.
SP_HILL_RHO0: float = 386.25

#: Hill-curve steepness (dimensionless).
SP_HILL_GAMMA: float = 0.2886


def calculate_speed_score_relative(ratio: float) -> float:
    """Cross-machine speed score relative to the MinaCalc baseline.

    ``ratio = v_algo / v_baseline``.  Hill curve::

        score = 100 / (1 + (ratio / 386.25) ** 0.2886)

    The curve asymptotes to 100 as ``rho -> 0`` (instant completion), so a
    finite non-positive ratio is treated as the limit (100), not as 0.
    Non-finite input (NaN/inf) is a measurement failure and scores 0.
    """
    if not math.isfinite(ratio):
        return 0.0
    if ratio <= 0.0:
        # rho -> 0+ => score -> 100; a zero/negative reading can only come from
        # a sub-millisecond measurement, which is "faster than baseline".
        return 100.0
    return round(100.0 / (1.0 + (ratio / SP_HILL_RHO0) ** SP_HILL_GAMMA), 2)


def calculate_speed_score(v_ms_per_s: float) -> float:
    """Compute normalized 0-100 score from calculation speed (ms/s).

    Scoring standard (25 points per decade):
    - <= 0.015 ms/s: 100.0 pts
    - 0.15 ms/s:     75.0 pts
    - 1.50 ms/s:     50.0 pts
    - 15.0 ms/s:     25.0 pts
    - >= 150 ms/s:    0.0 pts

    Non-finite or negative inputs score 0: they mean "no valid measurement",
    and silently returning 100 is how a NaN produced a perfect SP reading.
    """
    try:
        value = float(v_ms_per_s)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value) or value < 0.0:
        return 0.0
    if value <= SPEED_FLOOR_MS_PER_S:
        return 100.0
    deduction = 25.0 * math.log10(max(1.0, value / SPEED_FLOOR_MS_PER_S))
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
    baseline_ms_per_s: Optional[float] = None,
    baseline_algorithm: str = "",
) -> SpeedResult:
    """Evaluate algorithmic computation speed across beatmaps.

    Parameters
    ----------
    timings : Sequence of (calc_time_ms, map_duration_seconds)
    platform_overhead_ms : Fixed per-invocation cost to subtract, in ms.
        Only for adapters whose difficulty path shells out to a binary that
        cannot run natively on this platform (Wine).  A sample whose measured
        time is *not* larger than the overhead is left untouched: that means the
        underlying call was served from cache (or ran natively).
    baseline_ms_per_s : Optional same-machine MinaCalc reading for rho scoring.

    Returns
    -------
    SpeedResult; ``speed_score`` is ``None`` when no valid sample exists.
    """
    overhead = max(0.0, float(platform_overhead_ms or 0.0))

    def _adjust(t_ms: float) -> float:
        # A sample that does not exceed the compatibility-layer cost never paid
        # it (cache hit or native run), so it must be kept verbatim.  The old
        # 25%-of-overhead rule subtracted the whole overhead from the 25%-100%
        # band, collapsing those samples to 0.001 ms and scoring them ~100.
        if overhead <= 0.0 or t_ms <= overhead:
            return t_ms
        return max(0.001, t_ms - overhead)

    valid: List[Tuple[float, float]] = []
    rejected = 0
    for t_ms, dur_s in timings:
        try:
            t_val = float(t_ms)
            d_val = float(dur_s)
        except (TypeError, ValueError):
            rejected += 1
            continue
        if not math.isfinite(t_val) or not math.isfinite(d_val) or d_val <= 0.0 or t_val < 0.0:
            rejected += 1
            continue
        valid.append((t_val, d_val))

    if not valid:
        return SpeedResult(
            average_speed_ms_per_s=0.0,
            median_speed_ms_per_s=0.0,
            realtime_factor=None,
            speed_score=None,
            evaluated_count=0,
            sample_speeds=[],
            platform_overhead_ms=round(overhead, 3),
            rejected_samples=rejected,
        )

    speeds = [max(0.001, _adjust(t_val)) / max(1.0, d_val) for t_val, d_val in valid]
    avg_speed = sum(speeds) / len(speeds)

    sorted_speeds = sorted(speeds)
    mid = len(sorted_speeds) // 2
    if len(sorted_speeds) % 2 == 1:
        med_speed = sorted_speeds[mid]
    else:
        med_speed = (sorted_speeds[mid - 1] + sorted_speeds[mid]) / 2.0

    # Score on the median: with a fixed marathon sample set the distribution is
    # tight, but a single pathological chart should not move the whole dimension.
    rf = 1000.0 / med_speed if med_speed > 0 else None
    # Cross-machine scoring: with a same-machine baseline supplied, score the
    # ratio (machine- and load-independent) instead of absolute ms/s.
    baseline_ratio: Optional[float] = None
    if baseline_ms_per_s and math.isfinite(float(baseline_ms_per_s)) and baseline_ms_per_s > 0.0 and med_speed > 0.0:
        baseline_ratio = med_speed / float(baseline_ms_per_s)
        score: Optional[float] = calculate_speed_score_relative(baseline_ratio)
    else:
        score = calculate_speed_score(med_speed)

    p95_speed = _percentile(sorted_speeds, 0.95)
    max_speed = sorted_speeds[-1]

    return SpeedResult(
        average_speed_ms_per_s=round(avg_speed, 4),
        median_speed_ms_per_s=round(med_speed, 4),
        realtime_factor=round(rf, 1) if rf is not None else None,
        speed_score=score,
        evaluated_count=len(speeds),
        sample_speeds=[round(s, 4) for s in speeds],
        p95_speed_ms_per_s=round(p95_speed, 4),
        max_speed_ms_per_s=round(max_speed, 4),
        platform_overhead_ms=round(overhead, 3),
        baseline_ratio=round(baseline_ratio, 4) if baseline_ratio is not None else None,
        baseline_ms_per_s=round(float(baseline_ms_per_s), 4) if baseline_ms_per_s else None,
        baseline_algorithm=baseline_algorithm,
        rejected_samples=rejected,
    )
