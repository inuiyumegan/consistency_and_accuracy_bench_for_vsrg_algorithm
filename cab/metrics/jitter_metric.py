"""Dimension 4: Anti-Jitter / Timing Perturbation Robustness Evaluator.

Evaluates two coupled properties (Scheme 2):
1. Micro-Jitter Stability (inside the OD 300 hit window, <= ~10ms):
   Perturbations within 25% of W_300(OD) are imperceptible to player accuracy,
   so a robust algorithm should exhibit small relative drift (MAPE).
2. Arrangement Sensitivity Probe (anti-density-blindness gate):
   Algorithms that merely count macro window note density (e.g. osu!lazer /
   osu!mania 400ms linear-decay sums) appear artificially immune to micro-jitter
   only because they ignore intra-window note timing arrangement altogether.
   When smooth streams are bunched into minijack bursts at invariant 400ms
   window density, genuine pattern/speed algorithms respond strongly (~12-23%),
   whereas density-only algorithms barely react (~1.5%) and are penalized via a
   continuous Hill capability gate G_arrange(probe_delta_pct).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
import math
import numpy as np

#: Half-response threshold (in %) for the intra-window arrangement probe gate.
#: Algorithms with < ~2% response to same-density burst bunching are heavily
#: discounted, while genuine arrangement-aware algorithms (12-23%) receive ~1.0.
ARRANGEMENT_PROBE_TAU_PCT: float = 3.5

#: Steepness exponent of the continuous Hill capability gate.
ARRANGEMENT_PROBE_EXPONENT: float = 2.5

# ---------------------------------------------------------------------------
# Direction-aware response target (2026-10-19)
# ---------------------------------------------------------------------------
# The probe nudges onsets by ~10 ms, well inside the hit window: it does not
# change what a player must physically do, so the *correct* response is no
# difficulty change at all.  ``r*`` (the clock rate that would reproduce the
# jittered reading on the original chart) therefore targets 1.0, and any
# reaction counts as an over-reaction.
#
# Falling below 1.0 is treated far more harshly than rising above it: a drop
# means the algorithm averaged the perturbation away instead of noticing it,
# which is a direction error rather than a calibration error.
RJ_TARGET_RSTAR: float = 1.0
#: Rise tolerance -- a mild over-reaction (<= +5%) is tolerated.
RJ_TOL_RISE: float = 0.05
#: Drop tolerance -- going the wrong way is penalised ~4x harder.
RJ_TOL_DROP: float = 0.012
#: Score floor, so the worst case stays legible instead of collapsing to ~0.
RJ_FLOOR: float = 10.0
#: How fast that floor decays on the *drop* side (r* < 1).  At the default the
#: floor is 10 * exp(-0.3/0.15) = 1.35 at r* = 0.7, i.e. a 30% inversion is
#: scored as a near-total failure instead of "0.1 of a perfect response".
RJ_FLOOR_DECAY: float = 0.15
#: Direction gate floor: an algorithm whose response direction is wrong on
#: every chart still keeps this fraction of its magnitude score.
RJ_DIRECTION_FLOOR: float = 0.25
#: Softens the arrangement gate so a density-only algorithm is discounted but
#: not driven to a meaningless sub-1 score.
RJ_GATE_EXPONENT: float = 0.5


def arrangement_capability_gate(probe_delta_pct: Optional[float]) -> float:
    """Continuous [0, 1] gate penalizing arrangement-blind window-density counters."""
    if probe_delta_pct is None:
        return 1.0
    val = max(0.0, float(probe_delta_pct))
    if val <= 1e-9:
        return 0.0
    ratio = (val / ARRANGEMENT_PROBE_TAU_PCT) ** ARRANGEMENT_PROBE_EXPONENT
    return float(ratio / (1.0 + ratio))


def jitter_score(
    mape: float,
    arrangement_sensitivity_pct: Optional[float] = None,
    rstar_median: Optional[float] = None,
    hardening_share: Optional[float] = None,
) -> float:
    """Compute the 0-100 Anti-Jitter score.

    Two modes:

    * **Direction-aware (preferred)** -- when ``rstar_median`` is available the
      score targets ``r* = 1.0`` (the jitter should not move the reading at
      all), with an asymmetric tolerance: +5% on the rise side, -1.2% on the
      drop side.  ``hardening_share`` gates the result so a split response
      (half up, half down) cannot score like a consistent one.
    * **Legacy stability** -- without ``r*`` it falls back to the old
      ``mape``-only curve, so historical artefacts remain scorable.

    The arrangement gate is square-rooted in both modes: the raw Hill gate is
    steep enough to drive density-only algorithms to ~0.1, which is not a
    meaningful reading.
    """
    # A NaN MAPE / r* is a failed measurement.  ``min(100.0, nan) == 100.0``
    # used to hand it a perfect score.
    if not math.isfinite(float(mape)):
        return 0.0
    if rstar_median is not None and not math.isfinite(float(rstar_median)):
        return 0.0
    if arrangement_sensitivity_pct is not None and not math.isfinite(float(arrangement_sensitivity_pct)):
        return 0.0

    gate = arrangement_capability_gate(arrangement_sensitivity_pct) ** RJ_GATE_EXPONENT

    if rstar_median is None:
        m = max(0.0, float(mape))
        base = 100.0 / (1.0 + (m / 4.0) ** 1.6)
        return max(0.0, min(100.0, base * gate))

    delta = float(rstar_median) - RJ_TARGET_RSTAR
    tolerance = RJ_TOL_RISE if delta >= 0 else RJ_TOL_DROP
    # The 10-point floor exists so a bad reading stays legible ("it responded,
    # just badly"), which is right for a calibration miss.  It is *not* right
    # for a deep drop: r* = 0.8 means a ~10 ms nudge made the chart read like a
    # 1.25x slowdown, i.e. the algorithm inverted the perturbation rather than
    # mis-scaling it.  Letting that keep 10 points made the 0.9-1.0 clamp and a
    # 0.7 reading score within 1 point of each other.  The floor therefore
    # decays with the drop; the rise side keeps it (over-reacting is a
    # calibration error, not a direction error).
    floor = RJ_FLOOR * math.exp(delta / RJ_FLOOR_DECAY) if delta < 0 else RJ_FLOOR
    base = floor + (100.0 - floor) / (1.0 + (delta / tolerance) ** 2)

    share = 1.0 if hardening_share is None else max(0.0, min(1.0, float(hardening_share)))
    base *= RJ_DIRECTION_FLOOR + (1.0 - RJ_DIRECTION_FLOOR) * share

    return max(0.0, min(100.0, base * gate))


@dataclass
class JitterResult:
    jitter_rmse: float
    mape: float  # Mean Absolute Percentage Error (%)
    max_absolute_delta: float
    max_relative_delta: float
    variance_perturbation: float
    num_evaluated_maps: int
    arrangement_sensitivity_pct: Optional[float] = None
    is_arrangement_blind: bool = False
    #: Share of beatmaps where the algorithm reports a *higher* difficulty after
    #: jitter.  Physically this is the expected direction: timing jitter breaks
    #: the rhythmic scaffolding, so a chart should not read as easier once its
    #: onsets are smeared.
    #: Median equivalent clock rate: the rate that would reproduce the jittered
    #: reading on the original chart.  1.0 means "the perturbation changed
    #: nothing", which is the physically expected outcome for a ~10 ms nudge.
    rstar_median: Optional[float] = None
    #: Fraction of jittered charts whose ``r*`` could be solved (the rate curve
    #: must bracket the jittered reading; non-monotonic curves lose some).
    rstar_coverage: float = 0.0
    hardening_share: float = 0.0
    #: Share reporting a *lower* difficulty after jitter.  A high value means
    #: the algorithm averages the perturbation away instead of noticing the
    #: smearing.  Diagnostic only -- no score consumes it yet (the asymmetric
    #: penalty is pending a decision on its multiplier, see the run notes for
    #: 2026-10-19).
    softening_share: float = 0.0
    #: Charts the probe intended to score.  ``num_evaluated_maps`` alone used to
    #: be the only count, so a chart whose perturbed reading came back as None
    #: (e.g. an adapter whose persistent server died mid-run) disappeared
    #: without a trace and the dimension silently scored a smaller sample.
    #: 0 means "not recorded" (older runs).
    num_expected_maps: int = 0
    #: Names of the probe charts that produced no reading, so the drop is
    #: visible instead of only being inferable from a smaller N.
    dropped_maps: Sequence[str] = ()

    @property
    def probe_coverage(self) -> Optional[float]:
        """Share of the intended probe that actually produced a reading."""
        if self.num_expected_maps <= 0:
            return None
        return self.num_evaluated_maps / self.num_expected_maps

    @property
    def score(self) -> float:
        if self.num_evaluated_maps == 0:
            return 0.0
        return jitter_score(
            self.mape,
            self.arrangement_sensitivity_pct,
            rstar_median=self.rstar_median,
            hardening_share=self.hardening_share,
        )


def evaluate_jitter(
    orig_difficulties: Sequence[float],
    jitter_difficulties: Sequence[float],
    arrangement_sensitivity_pct: Optional[float] = None,
    rstar_median: Optional[float] = None,
    rstar_coverage: float = 0.0,
    num_expected_maps: int = 0,
    dropped_maps: Sequence[str] = (),
) -> JitterResult:
    """Evaluate algorithmic sensitivity to OD-adaptive micro timing jitter."""
    orig = np.array(orig_difficulties, dtype=float)
    jitt = np.array(jitter_difficulties, dtype=float)

    if len(orig) == 0:
        return JitterResult(
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0,
            arrangement_sensitivity_pct,
            False,
            num_expected_maps=max(num_expected_maps, 0),
            dropped_maps=tuple(dropped_maps),
        )

    deltas = jitt - orig
    abs_deltas = np.abs(deltas)

    # Shift-safe denominator prevents near-zero / negative ND baseline blowup
    d_min = float(min(0.0, np.min(orig)))
    denom = np.maximum(orig - d_min, 1.0)
    rel_deltas = abs_deltas / denom

    rmse = float(np.sqrt(np.mean(deltas ** 2)))
    mape = float(np.mean(rel_deltas)) * 100.0  # as percentage
    max_abs = float(np.max(abs_deltas))
    max_rel = float(np.max(rel_deltas))
    var = float(np.var(deltas))
    n_maps = len(deltas)
    hardening_share = float(np.sum(deltas > 1e-12)) / n_maps
    softening_share = float(np.sum(deltas < -1e-12)) / n_maps
    is_blind = (
        arrangement_sensitivity_pct is not None
        and float(arrangement_sensitivity_pct) < 3.0
    )

    return JitterResult(
        jitter_rmse=rmse,
        mape=mape,
        max_absolute_delta=max_abs,
        max_relative_delta=max_rel,
        variance_perturbation=var,
        num_evaluated_maps=len(orig),
        arrangement_sensitivity_pct=arrangement_sensitivity_pct,
        is_arrangement_blind=is_blind,
        rstar_median=rstar_median,
        rstar_coverage=rstar_coverage,
        hardening_share=hardening_share,
        softening_share=softening_share,
        num_expected_maps=max(num_expected_maps, 0),
        dropped_maps=tuple(dropped_maps),
    )

