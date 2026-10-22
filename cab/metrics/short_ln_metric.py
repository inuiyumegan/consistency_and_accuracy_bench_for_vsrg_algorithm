"""Dimension 5: Anti-Short LN / Release-Tap Artifact Robustness Evaluator.

Evaluates whether algorithms intelligently handle short Long Notes (20ms-100ms)
without artificial release-tail inflation, coupled with a continuous Hold-Off
(HO) probe capability gate (Scheme 2):

1. Two-sided tolerance band on Short-LN Inflation (ITR):
   Injecting 20-100ms LNs adds real release structure to a rice chart, so a
   faithful release model raises difficulty by a moderate, realistic margin
   (centred at ``TARGET_INFLATION_PCT``) while suppressing runaway release-tail
   inflation.
2. Continuous HO Probe Capability Gate (anti-LN-blindness gate):
   Algorithms that completely ignore LN tails (``ho_delta_pct == 0%`` on full
   LN maps, e.g. Daniel, MinaCalc, Interlude) would otherwise score ~56 pts on
   the two-sided band alone. Multiplying by the smooth Hill gate
   ``G_LN(ho_delta_pct)`` drives truly LN-blind algorithms to 0.0 pts while
   giving smooth proportional credit to partial LN models and ~1.0 to full LN
   models (15-30% HO response).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
import math
import numpy as np

#: Inflation (in %) a faithful release model is expected to show when 20-100ms
#: LNs are injected into a rice chart. Centred on the observed "realistic
#: overhead" cluster of the reference algorithm set (≈6-9%).
TARGET_INFLATION_PCT: float = 7.0

#: Decay scale of the two-sided band, in inflation percentage points. A gap of
#: this size costs half the score (the logistic is normalised to 1 at the
#: centre), so ~8 points off-target is already a substantial deduction.
INFLATION_TOLERANCE_PCT: float = 8.0

#: Shape exponent. >1 keeps the band flat near the target and steep far from it.
INFLATION_EXPONENT: float = 1.8

#: Half-response threshold (in %) for the full-LN Hold-Off (HO) capability gate.
HO_PROBE_TAU_PCT: float = 5.0

# ---------------------------------------------------------------------------
# Equivalent-rate scoring (2026-10-19)
# ---------------------------------------------------------------------------
# The score is now driven by ``r*``: the clock rate at which the *original*
# chart would read the same difficulty as the short-LN version.  ``r* = 1.0``
# means "shortening the LNs changed nothing", which is the target.
#
# Why 1.0 rather than the old +7% ITR target: a 49-59 ms LN needs no change in
# hold habit, and the release judgement of an LN is more forgiving than a tap
# while contributing its own accuracy weight -- so short-LN-ing a chart tends to
# *lower* the effective difficulty for a human.  Any rise is therefore a
# modelling error, not a legitimate LN cost.
#
# Symmetric tolerance, because the perturbation can plausibly move difficulty
# either way: the old ITR target asserted a direction that the data does not
# support.
SLN_TARGET_RSTAR: float = 1.0
SLN_TOL: float = 0.10

#: Steepness exponent of the continuous HO capability gate.
HO_PROBE_EXPONENT: float = 2.5


def ln_capability_gate(
    ho_delta_pct: Optional[float] = None,
    is_brute_force_tap: bool = False,
) -> float:
    """Continuous [0, 1] capability gate from the full-LN Hold-Off (HO) probe."""
    if ho_delta_pct is not None:
        val = max(0.0, float(ho_delta_pct))
        if val <= 1e-9:
            return 0.0
        ratio = (val / HO_PROBE_TAU_PCT) ** HO_PROBE_EXPONENT
        return float(ratio / (1.0 + ratio))
    if is_brute_force_tap:
        return 0.0
    return 1.0


def short_ln_score(
    inflation_pct: float,
    ho_delta_pct: Optional[float] = None,
    is_brute_force_tap: bool = False,
    rstar_median: Optional[float] = None,
) -> float:
    """Score the short-LN response.

    Two modes:

    * **Equivalent-rate (preferred)** -- with ``rstar_median`` the score targets
      ``r* = 1.0`` symmetrically: shortening the LNs should not move the
      reading at all, and a departure in either direction is equally wrong.
    * **Legacy ITR band** -- without ``r*`` it falls back to the old two-sided
      band around ``TARGET_INFLATION_PCT``, keeping historical artefacts
      scorable.

    In both modes the result is multiplied by the Hold-Off capability gate: an
    algorithm that cannot rate LNs at all is not "perfectly stable", it simply
    never saw the perturbation.
    """
    # Non-finite inputs are measurement failures, not perfect stability:
    # ``min(100.0, nan)`` evaluates to 100.0 (CODE_REVIEW P0-02).
    if not math.isfinite(float(inflation_pct)):
        return 0.0
    if rstar_median is not None and not math.isfinite(float(rstar_median)):
        return 0.0

    gate = ln_capability_gate(ho_delta_pct, is_brute_force_tap=is_brute_force_tap)
    if not math.isfinite(float(gate)):
        return 0.0

    if rstar_median is None:
        deviation = abs(float(inflation_pct) - TARGET_INFLATION_PCT)
        base = 100.0 / (1.0 + (deviation / INFLATION_TOLERANCE_PCT) ** INFLATION_EXPONENT)
    else:
        delta = float(rstar_median) - SLN_TARGET_RSTAR
        base = 100.0 / (1.0 + (delta / SLN_TOL) ** 2)

    return max(0.0, min(100.0, base * gate))


def short_ln_grade(
    inflation_pct: float,
    ln_excluded: bool = False,
    ho_delta_pct: Optional[float] = None,
    is_brute_force_tap: bool = False,
) -> str:
    """Grade label for the two-sided band + HO capability probe."""
    if ln_excluded:
        return "F (LN Excluded by Tester / Rice-Only Domain)"
    if is_brute_force_tap or (ho_delta_pct is not None and float(ho_delta_pct) < 2.0):
        return "F (Brute-Force LN Stripping / LN-Blind)"
    deviation = abs(float(inflation_pct) - TARGET_INFLATION_PCT)
    if deviation <= 2.0:
        return "S (On Target / Realistic Release Overhead)"
    if deviation <= 5.0:
        return "A (Near Realistic Band)"
    if deviation <= 10.0:
        return "B (Moderate Deviation from Realistic Band)"
    if deviation <= 20.0:
        return "C (Strong Deviation from Realistic Band)"
    return "F (Severe Deviation from Realistic Band)"


@dataclass
class ShortLNResult:
    short_ln_rmse: float
    inflation_tendency_ratio: float  # Percentage (%)
    max_absolute_delta: float
    has_native_immunity: bool
    immunity_grade: str
    num_evaluated_maps: int
    is_brute_force_tap: bool = False
    ln_excluded: bool = False
    ho_delta_pct: Optional[float] = None
    #: Share of beatmaps whose difficulty *rose* after short-LN injection.
    #: Physically the expected direction: a 20-100 ms LN still adds something to
    #: read and press, so a chart should not get easier.
    inflation_share: float = 0.0
    #: Share whose difficulty *fell* after injection.  ``inflation_tendency_ratio``
    #: is a mean, so it is blind to this: 48% up + 48% down cancels to ~0 and
    #: reads as "far from the 7% target" rather than "no consistent response".
    deflation_share: float = 0.0
    #: Median equivalent clock rate: the rate at which the *original* chart
    #: reads the same as its short-LN version.  1.0 = the perturbation changed
    #: nothing, which is the target (see SLN_TARGET_RSTAR).
    rstar_median: Optional[float] = None
    #: Fraction of short-LN charts whose r* could be solved.
    rstar_coverage: float = 0.0
    #: Charts the probe intended to score.  ``num_evaluated_maps`` alone used to
    #: be the only count, so a chart whose mutated reading came back as None
    #: (crashed adapter) vanished without a trace.  0 means "not recorded".
    num_expected_maps: int = 0
    #: Names of the probe charts that produced no reading.
    dropped_maps: Sequence[str] = ()

    @property
    def probe_coverage(self) -> Optional[float]:
        """Share of the intended probe that actually produced a reading."""
        if self.num_expected_maps <= 0:
            return None
        return self.num_evaluated_maps / self.num_expected_maps

    @property
    def score(self) -> float:
        """0-100 sub-score on the two-sided band x HO capability gate."""
        if self.num_evaluated_maps == 0 or self.ln_excluded:
            return 0.0
        return short_ln_score(
            self.inflation_tendency_ratio,
            ho_delta_pct=self.ho_delta_pct,
            is_brute_force_tap=self.is_brute_force_tap,
            rstar_median=self.rstar_median,
        )


def evaluate_short_ln(
    orig_difficulties: Sequence[float],
    short_ln_difficulties: Sequence[float],
    is_brute_force_tap: bool = False,
    ln_excluded: bool = False,
    ho_delta_pct: Optional[float] = None,
    rstar_median: Optional[float] = None,
    rstar_coverage: float = 0.0,
    num_expected_maps: int = 0,
    dropped_maps: Sequence[str] = (),
) -> ShortLNResult:
    """Evaluate algorithmic sensitivity to short LN injection (20ms-100ms).

    Parameters
    ----------
    orig_difficulties : difficulties of original rice maps
    short_ln_difficulties : difficulties of mutated short-LN maps
    is_brute_force_tap : True if algorithm naively strips all LNs to tap notes (HO probe)
    ln_excluded : True if tester completely excluded LN folder from benchmark
    ho_delta_pct : Mean relative change (%) on full-LN maps when LNs are converted to taps
    """
    orig = np.array(orig_difficulties, dtype=float)
    sln = np.array(short_ln_difficulties, dtype=float)

    if len(orig) == 0:
        return ShortLNResult(
            0.0, 0.0, 0.0, False, "F (No Evaluated Samples)", 0,
            is_brute_force_tap, ln_excluded, ho_delta_pct,
        )

    if len(orig) != len(sln):
        raise ValueError(
            f"evaluate_short_ln: {len(orig)} original vs {len(sln)} mutated "
            "difficulties; arrays must be aligned per map"
        )

    finite = np.isfinite(orig) & np.isfinite(sln)
    if not finite.all():
        orig = orig[finite]
        sln = sln[finite]
        if len(orig) == 0:
            raise ValueError("evaluate_short_ln: every sample was non-finite")

    deltas = sln - orig
    abs_deltas = np.abs(deltas)
    max_abs = float(np.max(abs_deltas))

    # Base check for numeric invariance
    raw_immune = max_abs < 1e-4

    rmse = float(np.sqrt(np.mean(deltas ** 2)))
    # Shift-safe denominator prevents near-zero / negative ND baseline blowup
    d_min = float(min(0.0, np.min(orig)))
    denom = np.maximum(orig - d_min, 1.0)
    rel_inflation = deltas / denom
    itr = float(np.mean(rel_inflation)) * 100.0  # as percentage

    n_maps = len(deltas)
    inflation_share = float(np.sum(deltas > 1e-9)) / n_maps
    deflation_share = float(np.sum(deltas < -1e-9)) / n_maps

    grade = short_ln_grade(
        itr,
        ln_excluded=ln_excluded,
        ho_delta_pct=ho_delta_pct,
        is_brute_force_tap=is_brute_force_tap,
    )

    return ShortLNResult(
        short_ln_rmse=rmse,
        inflation_tendency_ratio=itr,
        max_absolute_delta=max_abs,
        has_native_immunity=raw_immune,
        immunity_grade=grade,
        num_evaluated_maps=len(orig),
        is_brute_force_tap=is_brute_force_tap,
        ln_excluded=ln_excluded,
        ho_delta_pct=ho_delta_pct,
        inflation_share=inflation_share,
        deflation_share=deflation_share,
        rstar_median=rstar_median,
        rstar_coverage=rstar_coverage,
        num_expected_maps=max(num_expected_maps, 0),
        dropped_maps=tuple(dropped_maps),
    )

