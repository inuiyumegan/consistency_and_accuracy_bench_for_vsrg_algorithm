"""Standardized CAB-Score Composite Rating System (0 - 100 points).

Normalizes and synthesizes performance across all 6 benchmark dimensions:
1. Ground-Truth Accuracy & Calibrated ND-RMSE (0 - 100 pts, Weight: 50%)
2. Anti-Rate-Inversion Monotonicity (0 - 100 pts, Weight: 15%)
3. Anti-Short LN Release Artifact Robustness (0 - 100 pts, Weight: 15%)
4. Local Continuity & Cliffing Robustness (0 - 100 pts, Weight: 10%)
5. Algorithmic Computation Speed (0 - 100 pts, Weight: 5%)
6. Anti-Jitter Timing Perturbation Robustness (0 - 100 pts, Weight: 5%)

Weight ordering strictly adheres to:
    ACC (50%) > AIN (15%) = ASLN (15%) > CT (10%) > Speed (5%) = AJ (5%)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from cab.metrics.accuracy import AccuracyResult
from cab.metrics.continuity import ContinuityResult
from cab.metrics.inversion import InversionResult
from cab.metrics.jitter_metric import JitterResult
from cab.metrics.macro_inversion import macro_inversion_severity
from cab.metrics.short_ln_metric import ShortLNResult, short_ln_score
from cab.metrics.speed_metric import SpeedResult, calculate_speed_score


# ---------------------------------------------------------------------------
# Composite: ACC backbone with multiplicative robustness discounts
# ---------------------------------------------------------------------------
# The headline score is NOT a weighted average. Accuracy is the backbone and
# each robustness dimension only ever *discounts* it:
#
#     Total = ACC x PROD_i [ 1 - beta_i * (1 - s_i / 100) ]
#
# Consequences, all intentional:
#   * a dimension scoring 100 gives NO bonus -- it simply does not discount,
#     so saturated dimensions (6/10 algorithms hit exactly 100 on CT) can no
#     longer inflate everyone's total by a constant;
#   * the score is non-compensatory: a strong dimension cannot buy back a weak
#     one, which is what the old weighted sum allowed (osu!lazer ranked 3rd on
#     total while ranking 8th on accuracy);
#   * beta has a directly readable meaning: "if this dimension scored 0, the
#     total would be cut by beta_i".
#
# `beta` values are fixed constants, deliberately decoupled from any notion of
# "weight". Deriving them from weights makes the score *more* sensitive to
# parameter tweaking, not less (measured: +-50% perturbation moved 3.84 ranks
# with weight-derived betas vs 1.70 at +-0.05 absolute with fixed ones).
BETA_SCALE: float = 0.5

#: Base discount strengths. AIN and ASLN are pass/fail-shaped (monotonicity,
#: artifact inflation) so they carry the largest discounts; speed is a
#: performance property rather than a correctness one, hence the smallest.
BETA_BASE: Dict[str, float] = {
    "Anti-Rate-Inversion": 0.30,
    "Anti-Short LN": 0.30,
    "Local Continuity": 0.20,
    "Computation Speed": 0.10,
    "Anti-Jitter": 0.10,
}

BETA: Dict[str, float] = {k: v * BETA_SCALE for k, v in BETA_BASE.items()}

BETA_INVERSION: float = BETA["Anti-Rate-Inversion"]
BETA_SHORT_LN: float = BETA["Anti-Short LN"]
BETA_CONTINUITY: float = BETA["Local Continuity"]
BETA_SPEED: float = BETA["Computation Speed"]
BETA_JITTER: float = BETA["Anti-Jitter"]

# --- Accuracy (ACC) score curve parameters -----------------------------------
# Within the 40-point RMSE sub-score, the calibrated RMSE is converted through
#   40 * exp(-ACC_RMSE_DECAY * max(0, rmse - ACC_RMSE_BASELINE))
# The metric fed in is the *pattern-balanced* ``weighted_stratified_rmse``
# (fixed per-pattern weights, sample-count independent), NOT ``global_rmse``.
# The constants below were tuned on the reference algorithm set so that, with
# the balanced metric, the scores spread out instead of saturating.
ACC_RMSE_DECAY: float = 1.2
ACC_RMSE_BASELINE: float = 0.3

# --- Structural inversion ("倒挂", macro inversion) penalty, ACC-side v2 ------
# ACC additionally carries a penalty proportional to the tier-anchor inversion
# severity S (phase-averaged, coarse-grained dip at scales k = 2, 3, 5; see
# cab.metrics.macro_inversion).  This replaces the legacy clause removed on
# 2026-10-16: that one (min(17.5, alerts x 5)) saturated for every calibrated
# algorithm and exempted nd-native ones entirely; v2 differentiates and is
# computed from the cohort anchors recorded for every algorithm.
MACRO_INVERSION_PENALTY_SCALE: float = 90.0
MACRO_INVERSION_PENALTY_CAP: float = 30.0


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
    dimension_betas: Dict[str, float] = field(default_factory=dict)
    # Per-dimension discount factor actually applied (1.0 = no discount, or
    # None when the dimension was not evaluated in this slice).
    dimension_discounts: Dict[str, Optional[float]] = field(default_factory=dict)
    is_subtier: bool = False
    is_out_of_domain: bool = False
    macro_severity: float = 0.0
    macro_penalty: float = 0.0

    @property
    def dimension_weights(self) -> Dict[str, float]:
        """Deprecated alias of :attr:`dimension_betas` (kept for old callers)."""
        return self.dimension_betas


def score_accuracy(accuracy: AccuracyResult) -> Tuple[float, float, float]:
    """Return the Accuracy sub-score as ``(score, macro_severity, macro_penalty)``.

    Extracted so the *secondary* in-scope Accuracy reading is scored by exactly
    the same standard as the headline one -- including its **own** structural
    inversion penalty, recomputed from **its own** cohort anchors.  Reusing the
    full-run penalty would double-charge: a declaration that removes the charts
    responsible for a dip leaves an anchor sequence that is largely monotonic
    (measured on Dan-Overlay: S = 0.292 over the full set vs 0.021 in scope).

    Scoring rules (unchanged from the headline dimension):

    * Spearman ``rho`` contributes up to 60 pts  (``60 * rho**2``);
    * the *pattern-balanced* calibrated RMSE contributes up to 40 pts
      (``40 * exp(-1.2 * max(0, wRMSE - 0.3))``), deliberately **not**
      ``global_rmse`` -- see the note at the call site;
    * the structural-inversion ("倒挂") penalty is then subtracted, capped at
      ``MACRO_INVERSION_PENALTY_CAP``.
    """
    rho_score = 60.0 * max(0.0, accuracy.spearman_rho) ** 2
    rmse_score = 40.0 * math.exp(
        -ACC_RMSE_DECAY * max(0.0, accuracy.weighted_stratified_rmse - ACC_RMSE_BASELINE)
    )
    score = max(0.0, min(100.0, rho_score + rmse_score))

    calib = getattr(accuracy, "calibration", None)
    anchors = getattr(calib, "raw_anchors", None) or {}
    severity = macro_inversion_severity(anchors)
    penalty = min(MACRO_INVERSION_PENALTY_CAP, MACRO_INVERSION_PENALTY_SCALE * severity)

    return max(0.0, score - penalty), float(severity), float(penalty)


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
    """Compute the composite CAB-Score (0 - 100 points).

    ACC is the backbone; every robustness dimension can only discount it::

        Total = ACC x PROD_i [ 1 - beta_i * (1 - s_i / 100) ]

    - ``is_subtier=False`` (All Range): all six dimensions participate.
    - ``is_subtier=True`` (Number, Greek, Inhuman): the dynamic rate-scaling
      dimensions (AIN and CT) are not evaluated, so their discounts are simply
      absent from the product — no renormalisation is needed. This is a
      structural advantage over the old weighted sum, which had to
      re-proportion four weights to keep them summing to 1.00.
    - ``is_out_of_domain=True``: returns an N/A score for tiers outside the
      algorithm's calculation domain.

    The Accuracy dimension additionally carries the structural-inversion
    ("倒挂") penalty — see ``MACRO_INVERSION_PENALTY_*`` above.
    """
    if is_out_of_domain:
        betas = {
            "Anti-Short LN": BETA_SHORT_LN,
            "Computation Speed": BETA_SPEED,
            "Anti-Jitter": BETA_JITTER,
        }
        if not is_subtier:
            betas["Anti-Rate-Inversion"] = BETA_INVERSION
            betas["Local Continuity"] = BETA_CONTINUITY
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
            dimension_betas=betas,
            is_subtier=is_subtier,
            is_out_of_domain=True,
        )

    # 1. Ground-Truth Accuracy Score (0 - 100 pts)
    # Spearman rho provides up to 60 pts; the pattern-balanced calibrated RMSE
    # provides up to 40 pts.  The RMSE term deliberately uses
    # ``weighted_stratified_rmse`` rather than ``global_rmse``: the global RMSE
    # is a *sample-count-weighted* aggregation, which lets the per-pattern
    # sample distribution silently drive the score -- a large-n pattern an
    # algorithm handles well drags the metric down, while a small-n pattern
    # (e.g. ``unsigned``, 12 maps) is all but invisible no matter how bad it is.
    # ``weighted_stratified_rmse`` applies a *fixed* per-pattern weight that is
    # independent of sample counts, so no single pattern dominates by volume.
    #
    # The structural-inversion ("倒挂") penalty, v2, is folded in here: S is the
    # phase-averaged multiscale dip severity of the cohort anchors (scales
    # k = 2, 3, 5 -- micro-scale k = 1 jitter is folded away; see
    # cab.metrics.macro_inversion).  Cohort anchors are recorded for EVERY
    # algorithm -- including nd-native ones -- so no algorithm can dodge this
    # deduction; the legacy alert-count clause it replaces (removed 2026-10-16)
    # saturated for all calibrated algorithms and missed nd-native ones.
    s_acc, macro_severity, macro_penalty = score_accuracy(accuracy)

    # 2. Anti-Rate-Inversion Score (0 - 100 pts, All Range only)
    if not is_subtier and inversion is not None:
        if_rate = inversion.inversion_frequency
        auc_def = inversion.auc_deficit
        s_inv = 100.0 * math.exp(-20.0 * if_rate - 15.0 * auc_def)
        s_inv = max(0.0, min(100.0, s_inv))
    else:
        s_inv = None

    # 3. Anti-Short LN Score (0 - 100 pts)
    #
    # Two-sided tolerance band (see cab.metrics.short_ln_metric): injecting
    # 20-100ms LNs adds real objects, so a faithful release model must raise
    # difficulty by a realistic margin. Scoring is therefore maximised at
    # TARGET_INFLATION_PCT and decays symmetrically, which penalises
    # over-inflation (release-tail artifacts) *and* under-reaction alike.
    #
    # Consequences, both intentional:
    #   * brute-force LN-to-tap stripping is no longer a hard zero -- its ~0%
    #     inflation now reads as under-reaction (~56 pts) instead of "perfect
    #     immunity", which is what the old one-sided formula implied;
    #   * a zero-response probe no longer short-circuits to 100.
    #
    # Only "nothing was measured" (no samples, or the tester excluded LN from
    # the run) still scores 0 -- there is no reading to place on the band.
    if short_ln is not None:
        if getattr(short_ln, "ln_excluded", False) or getattr(short_ln, "num_evaluated_maps", 0) == 0:
            s_sln = 0.0
        else:
            s_sln = short_ln_score(short_ln.inflation_tendency_ratio)
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

    # Composite Score: ACC backbone x multiplicative robustness discounts.
    # A dimension that was not evaluated in this slice (None) simply contributes
    # no discount, so sub-tiers need no weight renormalisation.
    _discount_inputs = [
        ("Anti-Rate-Inversion", s_inv, BETA_INVERSION),
        ("Anti-Short LN", s_sln, BETA_SHORT_LN),
        ("Local Continuity", s_cont, BETA_CONTINUITY),
        ("Computation Speed", s_spd, BETA_SPEED),
        ("Anti-Jitter", s_jit, BETA_JITTER),
    ]
    total = s_acc
    weights: Dict[str, float] = {}
    discounts: Dict[str, Optional[float]] = {"Ground-Truth Accuracy": 1.0}
    for name, dim_score, beta in _discount_inputs:
        weights[name] = beta
        if dim_score is None:
            discounts[name] = None
            continue
        discount = 1.0 - beta * (1.0 - dim_score / 100.0)
        discounts[name] = discount
        total *= discount
    total = max(0.0, min(100.0, total))

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
        dimension_betas=weights,
        dimension_discounts=discounts,
        is_subtier=is_subtier,
        is_out_of_domain=False,
        macro_severity=round(macro_severity, 6),
        macro_penalty=round(macro_penalty, 2),
    )
