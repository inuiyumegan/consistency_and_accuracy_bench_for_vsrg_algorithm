"""Standardized CAB-Score Composite Rating System (0 - 100 points).

Normalizes and synthesizes performance across all 6 benchmark dimensions:
1. Ground-Truth Accuracy & Calibrated ND-RMSE (0 - 100 pts, Weight: 40%)
2. Anti-Rate-Inversion Monotonicity (0 - 100 pts, Weight: 20%)
3. Anti-Short LN Release Artifact Robustness (0 - 100 pts, Weight: 15%)
4. Local Continuity & Cliffing Robustness (0 - 100 pts, Weight: 10%)
5. Algorithmic Computation Speed (0 - 100 pts, Weight: 5%)
6. Anti-Jitter Timing Perturbation Robustness (0 - 100 pts, Weight: 10%)

Weight ordering strictly adheres to:
    ACC (40%) > AIN (20%) > ASLN (15%) > CT (10%) = AJ (10%) > Speed (5%)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional

from cab.metrics.accuracy import AccuracyResult
from cab.metrics.continuity import ContinuityResult
from cab.metrics.inversion import InversionResult
from cab.metrics.jitter_metric import JitterResult
from cab.metrics.short_ln_metric import ShortLNResult
from cab.metrics.speed_metric import SpeedResult, calculate_speed_score


# Composite Scoring Weights for All Range (6 dimensions, sum = 1.00)
WEIGHT_ACCURACY: float = 0.40
WEIGHT_INVERSION: float = 0.20
WEIGHT_SHORT_LN: float = 0.15
WEIGHT_CONTINUITY: float = 0.10
WEIGHT_SPEED: float = 0.05
WEIGHT_JITTER: float = 0.10

# Reallocated Sub-Tier Weights for Number, Greek, and Inhuman Tiers (4 dimensions, sum = 1.00)
# Dynamic rate scaling dimensions (Anti-Inversion 20% & Local Continuity 10%) are excluded from sub-tiers.
# The remaining four dimensions keep the SAME RATIOS as their All-Range weights and are
# proportionally renormalized to sum to 100% (base total = 0.40 + 0.15 + 0.05 + 0.10 = 0.70,
# i.e. ACC : ASLN : Speed : AJ = 40 : 15 : 5 : 10 = 57.14% : 21.43% : 7.14% : 14.29%):
_SUBTIER_WEIGHT_TOTAL: float = WEIGHT_ACCURACY + WEIGHT_SHORT_LN + WEIGHT_SPEED + WEIGHT_JITTER
WEIGHT_ACCURACY_SUBTIER: float = WEIGHT_ACCURACY / _SUBTIER_WEIGHT_TOTAL
WEIGHT_SHORT_LN_SUBTIER: float = WEIGHT_SHORT_LN / _SUBTIER_WEIGHT_TOTAL
WEIGHT_SPEED_SUBTIER: float = WEIGHT_SPEED / _SUBTIER_WEIGHT_TOTAL
WEIGHT_JITTER_SUBTIER: float = WEIGHT_JITTER / _SUBTIER_WEIGHT_TOTAL

# --- Accuracy (ACC) score curve parameters -----------------------------------
# Within the 40-point RMSE sub-score, the calibrated RMSE is converted through
#   40 * exp(-ACC_RMSE_DECAY * max(0, rmse - ACC_RMSE_BASELINE))
# The metric fed in is the *pattern-balanced* ``weighted_stratified_rmse``
# (fixed per-pattern weights, sample-count independent), NOT ``global_rmse``.
# The constants below were calibrated so that, with the balanced metric, the
# eight reference algorithms spread across ~[64, 82] instead of saturating.
ACC_RMSE_DECAY: float = 1.2
ACC_RMSE_BASELINE: float = 0.3


@dataclass
class ScoreBreakdown:
    s_accuracy: float
    s_inversion: Optional[float]
    s_short_ln: float
    s_continuity: Optional[float]
    s_speed: float
    s_jitter: float
    total_score: float
    dimension_scores: Dict[str, Optional[float]]
    dimension_weights: Dict[str, float] = field(default_factory=dict)
    is_subtier: bool = False
    is_out_of_domain: bool = False


def calculate_cab_score(
    accuracy: AccuracyResult,
    inversion: Optional[InversionResult] = None,
    continuity: Optional[ContinuityResult] = None,
    jitter: Optional[JitterResult] = None,
    short_ln: Optional[ShortLNResult] = None,
    speed: Optional[SpeedResult] = None,
    is_subtier: bool = False,
    is_out_of_domain: bool = False,
) -> ScoreBreakdown:
    """Compute normalized composite CAB-Score (0 - 100 points).

    - If is_subtier=False (All Range): Evaluates all 6 dimensions with weights
      ACC (40%), AIN (20%), ASLN (15%), CT (10%), Speed (5%), AJ (10%).
    - If is_subtier=True (Number, Greek, Inhuman): Excludes dynamic rate scaling (AIN & CT),
      reallocating the remaining 4 weights to keep their All-Range ratios and sum to 100%:
      ACC (57.14%), ASLN (21.43%), Speed (7.14%), AJ (14.29%).
    - If is_out_of_domain=True: Returns N/A score for tiers outside the algorithm's calculation domain.
    """
    if is_out_of_domain:
        weights = {
            "Ground-Truth Accuracy": WEIGHT_ACCURACY_SUBTIER if is_subtier else WEIGHT_ACCURACY,
            "Anti-Short LN": WEIGHT_SHORT_LN_SUBTIER if is_subtier else WEIGHT_SHORT_LN,
            "Computation Speed": WEIGHT_SPEED_SUBTIER if is_subtier else WEIGHT_SPEED,
            "Anti-Jitter": WEIGHT_JITTER_SUBTIER if is_subtier else WEIGHT_JITTER,
        }
        if not is_subtier:
            weights["Anti-Rate-Inversion"] = WEIGHT_INVERSION
            weights["Local Continuity"] = WEIGHT_CONTINUITY
        return ScoreBreakdown(
            s_accuracy=0.0,
            s_inversion=None,
            s_short_ln=0.0,
            s_continuity=None,
            s_speed=0.0,
            s_jitter=0.0,
            total_score=0.0,
            dimension_scores={
                "Ground-Truth Accuracy": None,
                "Anti-Rate-Inversion": None,
                "Anti-Short LN": None,
                "Local Continuity": None,
                "Computation Speed": None,
                "Anti-Jitter": None,
            },
            dimension_weights=weights,
            is_subtier=is_subtier,
            is_out_of_domain=True,
        )

    # 1. Ground-Truth Accuracy Score (0 - 100 pts)
    # Spearman rho provides up to 60 pts; the pattern-balanced calibrated RMSE
    # provides up to 40 pts.
    #
    # The RMSE term deliberately uses ``weighted_stratified_rmse`` rather than
    # ``global_rmse``.  The global RMSE is a *sample-count-weighted* aggregation
    # (rmse_global = sqrt(sum_p n_p * rmse_p^2 / N)), which lets the per-pattern
    # sample distribution silently drive the score: a large-n pattern that an
    # algorithm happens to handle well drags the metric down, while a small-n
    # pattern (e.g. ``unsigned``, 12 maps) is all but invisible no matter how bad
    # it is.  ``weighted_stratified_rmse`` instead applies a *fixed* per-pattern
    # weight that is independent of sample counts, so no single pattern can
    # dominate by sheer volume.  See ``AccuracyResult.weighted_stratified_rmse``.
    rho_score = 60.0 * max(0.0, accuracy.spearman_rho) ** 2
    rmse_score = 40.0 * math.exp(
        -ACC_RMSE_DECAY * max(0.0, accuracy.weighted_stratified_rmse - ACC_RMSE_BASELINE)
    )
    s_acc = max(0.0, min(100.0, rho_score + rmse_score))

    # NOTE: the legacy "macro inversion penalty" (-min(17.5, alerts x 5)) was
    # removed on 2026-10-16.  In practice every calibrated (non-ng) algorithm
    # fired dozens of adjacent-tier alerts (42-54 in the reference set), so the
    # clause always hit its -17.5 cap: a constant, non-discriminating deduction
    # that never applied to ng-native algorithms anyway (identity calibration
    # produces no alerts).  The alerts themselves are still recorded and shown
    # as diagnostics; they simply no longer affect any score.

    # 2. Anti-Rate-Inversion Score (0 - 100 pts, All Range only)
    if not is_subtier and inversion is not None:
        if_rate = inversion.inversion_frequency
        auc_def = inversion.auc_deficit
        s_inv = 100.0 * math.exp(-20.0 * if_rate - 15.0 * auc_def)
        s_inv = max(0.0, min(100.0, s_inv))
    else:
        s_inv = None

    # 3. Anti-Short LN Score (0 - 100 pts)
    if short_ln is not None:
        if getattr(short_ln, "is_brute_force_tap", False) or getattr(short_ln, "ln_excluded", False):
            s_sln = 0.0  # Zero points if algorithm brute-force strips LN or tester excludes LN
        elif getattr(short_ln, "num_evaluated_maps", 0) == 0:
            s_sln = 0.0
        elif short_ln.has_native_immunity:
            s_sln = 100.0
        else:
            itr = abs(short_ln.inflation_tendency_ratio)
            s_sln = 100.0 / (1.0 + (itr / 18.0) ** 1.8)
        s_sln = max(0.0, min(100.0, s_sln))
    else:
        s_sln = 0.0

    # 4. Local Continuity Score (0 - 100 pts, All Range only)
    if not is_subtier and continuity is not None:
        s_cont = 100.0 * (continuity.smoothness_score / 100.0)
        s_cont = max(0.0, min(100.0, s_cont))
    else:
        s_cont = None

    # 5. Computation Speed Score (0 - 100 pts)
    if speed is not None:
        s_spd = speed.speed_score
    else:
        s_spd = 75.0  # Neutral baseline if speed not evaluated
    s_spd = max(0.0, min(100.0, s_spd))

    # 6. Anti-Jitter Score (0 - 100 pts)
    if jitter is not None:
        if getattr(jitter, "num_evaluated_maps", 0) == 0:
            s_jit = 0.0
        else:
            mape = max(0.0, jitter.mape)
            s_jit = 100.0 / (1.0 + (mape / 4.0) ** 1.6)
        s_jit = max(0.0, min(100.0, s_jit))
    else:
        s_jit = 0.0

    # Weighted Composite Score (0 - 100 pts)
    if is_subtier:
        total = (
            WEIGHT_ACCURACY_SUBTIER * s_acc
            + WEIGHT_SHORT_LN_SUBTIER * s_sln
            + WEIGHT_SPEED_SUBTIER * s_spd
            + WEIGHT_JITTER_SUBTIER * s_jit
        )
        weights = {
            "Ground-Truth Accuracy": WEIGHT_ACCURACY_SUBTIER,
            "Anti-Short LN": WEIGHT_SHORT_LN_SUBTIER,
            "Computation Speed": WEIGHT_SPEED_SUBTIER,
            "Anti-Jitter": WEIGHT_JITTER_SUBTIER,
        }
    else:
        total = (
            WEIGHT_ACCURACY * s_acc
            + WEIGHT_INVERSION * (s_inv if s_inv is not None else 0.0)
            + WEIGHT_SHORT_LN * s_sln
            + WEIGHT_CONTINUITY * (s_cont if s_cont is not None else 0.0)
            + WEIGHT_SPEED * s_spd
            + WEIGHT_JITTER * s_jit
        )
        weights = {
            "Ground-Truth Accuracy": WEIGHT_ACCURACY,
            "Anti-Rate-Inversion": WEIGHT_INVERSION,
            "Anti-Short LN": WEIGHT_SHORT_LN,
            "Local Continuity": WEIGHT_CONTINUITY,
            "Computation Speed": WEIGHT_SPEED,
            "Anti-Jitter": WEIGHT_JITTER,
        }

    dim_scores: Dict[str, Optional[float]] = {
        "Ground-Truth Accuracy": round(s_acc, 2),
        "Anti-Short LN": round(s_sln, 2),
        "Computation Speed": round(s_spd, 2),
        "Anti-Jitter": round(s_jit, 2),
    }
    if not is_subtier:
        dim_scores["Anti-Rate-Inversion"] = round(s_inv, 2) if s_inv is not None else None
        dim_scores["Local Continuity"] = round(s_cont, 2) if s_cont is not None else None
    else:
        dim_scores["Anti-Rate-Inversion"] = None
        dim_scores["Local Continuity"] = None

    return ScoreBreakdown(
        s_accuracy=round(s_acc, 2),
        s_inversion=round(s_inv, 2) if s_inv is not None else None,
        s_short_ln=round(s_sln, 2),
        s_continuity=round(s_cont, 2) if s_cont is not None else None,
        s_speed=round(s_spd, 2),
        s_jitter=round(s_jit, 2),
        total_score=round(total, 2),
        dimension_scores=dim_scores,
        dimension_weights=weights,
        is_subtier=is_subtier,
        is_out_of_domain=False,
    )
