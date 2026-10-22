"""Structural ("macro") inversion severity for tier-anchor sequences.

A calibrated algorithm's cohort centroids (the per-N-D-tier median readings)
should be monotonically non-decreasing with respect to the N-D tiers.  Where
they are not, the ordering is locally inverted -- in the project's shorthand,
a "倒挂" (macro inversion; the earlier term was 宏观层级倒吸).

This module quantifies the *severity* of that disorder in a way that is
robust to alignment and to micro-scale jitter:

  * **Coarse-graining at scale k**: merge consecutive k tiers into one bin
    (every phase offset phi in [0, k) is tried, then averaged), and sum the
    "dips" of the binned sequence (max(0, B_j - B_{j+1})), normalised by the
    anchor span.
  * **Severity S** = mean of the *evaluable* phase-averaged dips at scales
    k = 2, 3, 5.  A scale that cannot be evaluated on a short anchor sequence
    (k=5 needs >= 6 anchors) is excluded from the mean instead of contributing
    a hard 0 -- that hard 0 used to dilute the penalty on 3-anchor sequences
    and made 2-anchor sequences completely immune (CODE_REVIEW P1-04).
  * A two-anchor sequence is evaluated directly on its single adjacent pair.

Non-finite anchors (NaN/inf) are dropped; a NaN span used to produce
``min(30, 90*nan) == 30``, i.e. the maximum penalty, and was written into JSON.

Everything here is pure-Python and dependency-free so it can be unit-tested
in isolation.
"""

from __future__ import annotations

import math
from typing import List, Mapping, Sequence, Union

DEFAULT_SCALES = (2, 3, 5)

#: Anchors accepted by :func:`macro_inversion_severity`: a tier->centroid
#: mapping (sorted by tier internally) or an already tier-ordered sequence.
AnchorSource = Union[Mapping[float, float], Sequence[float]]


def multiscale_dip(values: Sequence[float], span: float, k: int) -> float:
    """Phase-averaged dip of the k-tier coarse-grained sequence, / span.

    ``values`` must be ordered by increasing N-D tier.  Each phase offset
    phi in [0, k) slices non-overlapping runs of k tiers (starting at phi); the
    runs are averaged into bins and the dips ``max(0, B_j - B_{j+1})`` are
    summed.  The per-phase results are averaged.  Returns 0.0 when no phase
    yields at least two bins.
    """
    if span <= 0 or not math.isfinite(span):
        return 0.0
    if k <= 1:
        k = 1

    phase_dips: List[float] = []
    for phi in range(k):
        chunks = [values[i : i + k] for i in range(phi, len(values), k)]
        chunks = [c for c in chunks if c]
        if len(chunks) < 2:
            continue
        bins = [sum(c) / len(c) for c in chunks]
        dip = sum(max(0.0, bins[j] - bins[j + 1]) for j in range(len(bins) - 1))
        phase_dips.append(dip / span)

    if not phase_dips:
        return 0.0
    return sum(phase_dips) / len(phase_dips)


def multiscale_dip_evaluable(values: Sequence[float], k: int) -> bool:
    """True when at least one phase can build two bins at scale ``k``."""
    if k <= 1:
        return len(values) >= 2
    for phi in range(k):
        chunks = [values[i : i + k] for i in range(phi, len(values), k)]
        chunks = [c for c in chunks if c]
        if len(chunks) >= 2:
            return True
    return False


def macro_inversion_severity(
    anchors: AnchorSource, scales: Sequence[int] = DEFAULT_SCALES
) -> float:
    """Phase-averaged multiscale inversion severity S of a tier-anchor sequence.

    ``anchors`` may be a ``{tier: centroid}`` mapping (the shape stored in
    ``CalibrationResult.raw_anchors``) or a sequence already ordered by tier.
    Mapping keys are sorted **numerically**, so JSON round-tripped string keys
    (``"9.0"``, ``"10.0"``) are ordered correctly instead of lexicographically.

    Returns 0.0 for fewer than two anchors or a degenerate (zero-span) set.
    """
    if isinstance(anchors, Mapping):
        try:
            ordered_keys = sorted(anchors.keys(), key=float)
        except (TypeError, ValueError):
            ordered_keys = sorted(anchors.keys())
        raw = [anchors[t] for t in ordered_keys]
    else:
        raw = list(anchors)

    values: List[float] = []
    for v in raw:
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            values.append(f)

    if len(values) < 2:
        return 0.0
    span = max(values) - min(values)
    if not math.isfinite(span) or span <= 0:
        return 0.0

    if len(values) == 2:
        # A single adjacent pair is a valid k=1 measurement.
        return max(0.0, values[0] - values[1]) / span

    evaluable = [k for k in scales if multiscale_dip_evaluable(values, k)]
    if not evaluable:
        return 0.0
    dips = [multiscale_dip(values, span, k) for k in evaluable]
    return sum(dips) / len(dips)
