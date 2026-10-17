"""Dimension 5: Anti-Short LN / Release-Tap Artifact Robustness Evaluator.

Evaluates whether algorithms intelligently handle short Long Notes (20ms-100ms)
without artificial release-tail inflation.

Two-sided tolerance band
------------------------
The score does **not** reward "no reaction". Injecting short LNs adds real
objects to the chart, so a faithful algorithm must raise its difficulty by a
realistic margin; a tool that reports an unchanged value is not immune but
blind to LNs. The score is therefore maximised at
``TARGET_INFLATION_PCT`` and decays symmetrically on both sides, so both
over-inflation (release-tail artifacts) and under-reaction (LN-deficient
pipelines) are penalised.

Anti-stripping note: brute-force LN-to-tap stripping is still detected and
reported (``is_brute_force_tap``), but it is no longer a hard zero — its
near-zero inflation simply lands on the under-reaction side of the band.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
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


def short_ln_score(inflation_pct: float) -> float:
    """Scores an inflation reading (in %) on the two-sided tolerance band.

    ``1.0`` at ``TARGET_INFLATION_PCT``, decaying symmetrically towards ``0``.
    Both signs are penalised equally.
    """
    deviation = abs(float(inflation_pct) - TARGET_INFLATION_PCT)
    return 100.0 / (1.0 + (deviation / INFLATION_TOLERANCE_PCT) ** INFLATION_EXPONENT)


def short_ln_grade(inflation_pct: float, ln_excluded: bool = False) -> str:
    """Grade label for the two-sided band (shared by reports and offline tools).

    Bands are symmetric around ``TARGET_INFLATION_PCT``: both over-inflation and
    under-reaction land in the same grade tier.
    """
    if ln_excluded:
        return "F (LN Excluded by Tester / Rice-Only Domain)"
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

    @property
    def score(self) -> float:
        """0-100 sub-score on the two-sided band (0.0 when nothing was measured)."""
        if self.num_evaluated_maps == 0 or self.ln_excluded:
            return 0.0
        return short_ln_score(self.inflation_tendency_ratio)


def evaluate_short_ln(
    orig_difficulties: Sequence[float],
    short_ln_difficulties: Sequence[float],
    is_brute_force_tap: bool = False,
    ln_excluded: bool = False,
) -> ShortLNResult:
    """Evaluate algorithmic sensitivity to short LN injection (20ms-100ms).

    Parameters
    ----------
    orig_difficulties : difficulties of original rice maps
    short_ln_difficulties : difficulties of mutated short-LN maps
    is_brute_force_tap : True if algorithm naively strips all LNs to tap notes (HO probe)
    ln_excluded : True if tester completely excluded LN folder from benchmark
    """
    orig = np.array(orig_difficulties, dtype=float)
    sln = np.array(short_ln_difficulties, dtype=float)

    if len(orig) == 0:
        return ShortLNResult(0.0, 0.0, 0.0, False, "F (No Evaluated Samples)", 0, is_brute_force_tap, ln_excluded)

    deltas = sln - orig
    abs_deltas = np.abs(deltas)
    max_abs = float(np.max(abs_deltas))

    # Base check for numeric invariance
    raw_immune = max_abs < 1e-4

    rmse = float(np.sqrt(np.mean(deltas ** 2)))
    rel_inflation = np.where(orig > 1e-6, deltas / orig, 0.0)
    itr = float(np.mean(rel_inflation)) * 100.0  # as percentage

    # Grade from the two-sided band. Brute-force stripping no longer forces a
    # failure grade; its near-zero inflation simply reads as under-reaction.
    # Only "nothing was measured" (LN excluded by the tester) stays a hard
    # failure -- the empty-sample case returned above.
    grade = short_ln_grade(itr, ln_excluded=ln_excluded)

    return ShortLNResult(
        short_ln_rmse=rmse,
        inflation_tendency_ratio=itr,
        max_absolute_delta=max_abs,
        # Informational only: the probe output did not move at all. Under the
        # two-sided band this is *under-reaction*, not immunity, so it no
        # longer short-circuits the score.
        has_native_immunity=raw_immune,
        immunity_grade=grade,
        num_evaluated_maps=len(orig),
        is_brute_force_tap=is_brute_force_tap,
        ln_excluded=ln_excluded,
    )
