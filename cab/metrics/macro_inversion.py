"""Structural ("macro") inversion severity for tier-anchor sequences.

A calibrated algorithm's cohort centroids (the per-ND-tier median readings)
should be monotonically non-decreasing with respect to the ND tiers.  Where
they are not, the ordering is locally inverted — in the project's shorthand,
a "倒挂" (macro inversion; the earlier term was 宏观层级倒吸).

This module quantifies the *severity* of that disorder in a way that is
robust to alignment and to micro-scale jitter:

  * **Coarse-graining at scale k**: merge consecutive k tiers into one bin
    (every phase offset φ ∈ [0, k) is tried, then averaged), and sum the
    "dips" of the binned sequence (max(0, B_j - B_{j+1})), normalised by the
    anchor span.  k = 1 equals the classic adjacent-pair dip sum; larger k
    folds short-wavelength jitter away and reveals trend inversion.
  * **Severity S** = mean of the phase-averaged dips at scales k = 2, 3, 5
    (k = 2/3 carries the structural signal; k = 5 catches long-range damage).

The scoring layer converts S into a penalty via ``min(CAP, SCALE * S)`` —
see ``cab.metrics.scoring``.

Everything here is pure-Python and dependency-free so it can be unit-tested
in isolation.
"""

from __future__ import annotations

from typing import Mapping, Sequence, Union

DEFAULT_SCALES = (2, 3, 5)

#: Anchors accepted by :func:`macro_inversion_severity`: a tier->centroid
#: mapping (sorted by tier internally) or an already tier-ordered sequence.
AnchorSource = Union[Mapping[float, float], Sequence[float]]


def multiscale_dip(values: Sequence[float], span: float, k: int) -> float:
    """Phase-averaged dip of the k-tier coarse-grained sequence, ÷ span.

    ``values`` must be ordered by increasing ND tier.  Each phase offset
    φ ∈ [0, k) slices non-overlapping runs of k tiers (starting at φ); the
    runs are averaged into bins and the dips ``max(0, B_j - B_{j+1})`` are
    summed.  The per-phase results are averaged.  Returns 0.0 when no phase
    yields at least two bins.
    """
    if span <= 0:
        return 0.0

    phase_dips: list[float] = []
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


def macro_inversion_severity(
    anchors: AnchorSource, scales: Sequence[int] = DEFAULT_SCALES
) -> float:
    """Phase-averaged multiscale inversion severity S of a tier-anchor sequence.

    ``anchors`` may be a ``{tier: centroid}`` mapping (the shape stored in
    ``CalibrationResult.raw_anchors``) or a sequence already ordered by tier.
    Returns 0.0 for fewer than two anchors or a degenerate (zero-span) set.
    """
    if isinstance(anchors, Mapping):
        values = [float(anchors[t]) for t in sorted(anchors)]
    else:
        values = [float(v) for v in anchors]

    if len(values) < 2:
        return 0.0
    span = max(values) - min(values)
    if span <= 0:
        return 0.0

    dips = [multiscale_dip(values, span, k) for k in scales]
    return sum(dips) / len(dips)
