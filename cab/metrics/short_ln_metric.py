"""Dimension 5: Anti-Short LN / Release-Tap Artifact Robustness Evaluator.

Evaluates whether algorithms intelligently handle short Long Notes (20ms-100ms)
without artificial release-tail inflation.

CRITICAL ANTI-STRIPPING RULE:
If the test suite completely excludes the LN dataset, or if the algorithm is
detected to perform brute-force LN-to-tap stripping (where LN beatmaps yield
virtually identical difficulty to their Hold-Off (HO) converted equivalents),
the algorithm is diagnosed as incapable of modeling LN semantics. In such cases,
it CANNOT claim short-LN immunity, and its Anti-Short LN score is strictly set to 0.0 pts.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np


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

    # Enforce Anti-Stripping Guard
    if ln_excluded:
        is_immune = False
        grade = "F (LN Excluded by Tester / Rice-Only Domain)"
    elif is_brute_force_tap:
        is_immune = False
        grade = "F (Brute-Force LN-to-Tap Stripping / LN Deficient)"
    elif raw_immune:
        is_immune = True
        grade = "S+ (Theoretical Optimum / Native Filter)"
    elif abs(itr) < 1.0 and rmse < 0.1:
        is_immune = False
        grade = "S (Highly Robust / Minimal Inflation)"
    elif abs(itr) <= 18.0:
        is_immune = False
        grade = "A (Realistic LN Release Overhead)"
    elif abs(itr) <= 35.0:
        is_immune = False
        grade = "B (Moderate Release Overhead)"
    elif abs(itr) <= 60.0:
        is_immune = False
        grade = "C (High Release Artifact Inflation)"
    else:
        is_immune = False
        grade = "F (Runaway Artifact Inflation)"

    return ShortLNResult(
        short_ln_rmse=rmse,
        inflation_tendency_ratio=itr,
        max_absolute_delta=max_abs,
        has_native_immunity=is_immune,
        immunity_grade=grade,
        num_evaluated_maps=len(orig),
        is_brute_force_tap=is_brute_force_tap,
        ln_excluded=ln_excluded,
    )
