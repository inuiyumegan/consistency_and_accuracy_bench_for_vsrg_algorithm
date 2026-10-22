"""Standardized CAB-Score Composite Rating System (0 - 100 points).

Synthesizes all 6 benchmark dimensions multiplicatively:

    Total = ACC x PROD_i [ 1 - beta_i * (1 - s_i / 100) ]

Dimensions and their discount strengths (``BETA_BASE`` x ``BETA_SCALE``):

1. Ground-Truth Accuracy       (backbone, no beta)
2. Anti-Rate-Inversion  AIN    beta = 15%
3. Anti-Short LN        ASLN   beta = 15%
4. Local Continuity     CT     beta = 10%
5. Computation Speed    SP     beta =  5%
6. Anti-Jitter          AJ     beta =  5%

A dimension is a **discount**, not a weight: scoring 100 gives no bonus, it
simply does not discount.  ``None`` means "not evaluated in this slice" and
contributes no discount whatsoever -- it must never be silently replaced by 0
(which would fake a 15% penalty) or by 100 (which would fake perfection).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from cab.metrics.accuracy import AccuracyResult
from cab.metrics.continuity import ContinuityResult
from cab.metrics.inversion import InversionResult
from cab.metrics.jitter_metric import JitterResult, jitter_score
from cab.metrics.macro_inversion import macro_inversion_severity
from cab.metrics.short_ln_metric import ShortLNResult, short_ln_score
from cab.metrics.speed_metric import SpeedResult


# ---------------------------------------------------------------------------
# Composite: ACC backbone with multiplicative robustness discounts
# ---------------------------------------------------------------------------
# The headline score is NOT a weighted average. Accuracy is the backbone and
# each robustness dimension only ever *discounts* it.
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
ACC_RMSE_DECAY: float = 1.2
ACC_RMSE_BASELINE: float = 0.3

# --- Structural inversion ("倒挂", macro inversion) penalty, ACC-side v2 ------
MACRO_INVERSION_PENALTY_SCALE: float = 90.0
MACRO_INVERSION_PENALTY_CAP: float = 30.0


def _finite(value: Optional[float]) -> Optional[float]:
    """Return ``value`` when it is a finite float, else ``None``.

    Guarding here (instead of relying on ``max()/min()``) matters: in Python
    ``max(0.0, nan) == 0.0`` and ``min(100.0, nan) == 100.0``, so a NaN used to
    read as *perfect* on every dimension.
    """
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


@dataclass
class ScoreBreakdown:
    s_accuracy: float
    s_inversion: Optional[float]
    s_short_ln: Optional[float]
    s_continuity: Optional[float]
    s_speed: Optional[float]
    s_jitter: Optional[float]
    total_score: float
    dimension_scores: Dict[str, Optional[float]]
    dimension_betas: Dict[str, float] = field(default_factory=dict)
    # Per-dimension discount factor actually applied (1.0 = no discount, or
    # None when the dimension was not evaluated in this slice).
    dimension_discounts: Dict[str, Optional[float]] = field(default_factory=dict)
    is_subtier: bool = False
    is_out_of_domain: bool = False
    #: False when the Accuracy backbone itself had no evaluated sample.  Such a
    #: run must never be published as a number; callers should abort before
    #: writing artefacts (suite enforces this).
    is_evaluated: bool = True
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
    inversion penalty, recomputed from **its own** cohort anchors.

    Scoring rules:

    * Spearman ``rho`` contributes up to 60 pts  (``60 * rho**2``);
    * the *pattern-balanced* calibrated RMSE contributes up to 40 pts
      (``40 * exp(-1.2 * max(0, wRMSE - 0.3))``);
    * the structural-inversion ("倒挂") penalty is then subtracted, capped at
      ``MACRO_INVERSION_PENALTY_CAP``.

    Returns ``(0.0, 0.0, 0.0)`` when the accuracy result carries no samples or
    non-finite statistics; ``calculate_cab_score`` propagates that as
    ``is_evaluated=False`` rather than pretending the algorithm scored 40.
    """
    num_samples = getattr(accuracy, "num_samples", None)
    if num_samples is not None and int(num_samples) == 0:
        return 0.0, 0.0, 0.0

    rho = _finite(accuracy.spearman_rho)
    wrmse = _finite(accuracy.weighted_stratified_rmse)
    if rho is None or wrmse is None:
        # Non-finite statistics are a measurement failure, not a perfect score.
        return 0.0, 0.0, 0.0

    rho_score = 60.0 * max(0.0, rho) ** 2
    rmse_score = 40.0 * math.exp(
        -ACC_RMSE_DECAY * max(0.0, wrmse - ACC_RMSE_BASELINE)
    )
    score = max(0.0, min(100.0, rho_score + rmse_score))

    calib = getattr(accuracy, "calibration", None)
    anchors = getattr(calib, "raw_anchors", None) or {}
    severity = macro_inversion_severity(anchors)
    severity = _finite(severity) or 0.0
    penalty = min(MACRO_INVERSION_PENALTY_CAP, MACRO_INVERSION_PENALTY_SCALE * severity)

    return max(0.0, score - penalty), float(severity), float(penalty)


def _dimension_score(obj, score_attr: str, count_attr: str = "evaluated_count") -> Optional[float]:
    """Score of one dimension, or ``None`` when it was not evaluated.

    ``None`` is the *only* correct value for "no data": it contributes no
    discount and is rendered as "—" in reports.
    """
    if obj is None:
        return None
    count = getattr(obj, count_attr, None)
    try:
        if count is not None and int(count) <= 0:
            return None
    except (TypeError, ValueError):
        pass
    return _finite(getattr(obj, score_attr, None))


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
      dimensions (AIN and CT) are not evaluated, and Speed (SP) is an
      algorithm-level metric measured once on the global probe, so it does not
      participate in sub-tier scoring either.
    - ``is_out_of_domain=True``: returns an N/A score for tiers outside the
      algorithm's calculation domain.
    - A dimension whose data is missing (``None``, or zero evaluated samples)
      contributes **no discount** and is reported as ``None``.
    """
    if is_out_of_domain:
        betas = {
            "Anti-Short LN": BETA_SHORT_LN,
            "Anti-Jitter": BETA_JITTER,
        }
        if not is_subtier:
            betas["Computation Speed"] = BETA_SPEED
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
            is_evaluated=False,
        )

    # 1. Ground-Truth Accuracy Score (0 - 100 pts)
    s_acc_raw, macro_severity, macro_penalty = score_accuracy(accuracy)
    num_samples = getattr(accuracy, "num_samples", None)
    accuracy_evaluated = not (num_samples is not None and int(num_samples) == 0)
    s_acc = round(s_acc_raw, 2) if accuracy_evaluated else 0.0

    if not accuracy_evaluated:
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
            dimension_betas={},
            is_subtier=is_subtier,
            is_out_of_domain=False,
            is_evaluated=False,
        )

    # 2. Anti-Rate-Inversion Score (1 - 100 pts, All Range only)
    #
    # ``if_rate`` is the rate-span-weighted fraction of [r_min, r_max] with
    # negative slope beyond the micro-deadband, and ``auc_def`` (IDA) is the
    # normalized trapezoidal integral of the running-peak relative drawdown
    # envelope M(r) - D(r). The score decays hyperbolically:
    # S = 100 / (1 + k/2), k = 25*IF + 120*IDA.
    if not is_subtier and inversion is not None and int(getattr(inversion, "total_maps_evaluated", 0) or 0) > 0:
        if_rate = _finite(inversion.inversion_frequency) or 0.0
        auc_def = _finite(inversion.auc_deficit) or 0.0
        deficit = 25.0 * if_rate + 120.0 * auc_def
        s_inv = 100.0 / (1.0 + 0.5 * deficit)
        s_inv = max(1.0, min(100.0, s_inv))
    else:
        s_inv = None

    # 3. Anti-Short LN Score (0 - 100 pts)
    # No evaluated samples (or LN facts excluded by the tester) => not evaluated.
    s_sln: Optional[float] = None
    if short_ln is not None and not getattr(short_ln, "ln_excluded", False):
        if int(getattr(short_ln, "num_evaluated_maps", 0) or 0) > 0:
            s_sln = short_ln_score(
                short_ln.inflation_tendency_ratio,
                ho_delta_pct=getattr(short_ln, "ho_delta_pct", None),
                is_brute_force_tap=getattr(short_ln, "is_brute_force_tap", False),
                rstar_median=getattr(short_ln, "rstar_median", None),
            )
            s_sln = max(0.0, min(100.0, s_sln))

    # 4. Local Continuity Score (0 - 100 pts, All Range only)
    if not is_subtier and continuity is not None:
        cont_count = getattr(continuity, "evaluated_maps", None)
        if cont_count is not None and int(cont_count) == 0:
            s_cont = None
        else:
            s_cont = _finite(continuity.smoothness_score)
            s_cont = max(0.0, min(100.0, s_cont)) if s_cont is not None else None
    else:
        s_cont = None

    # 5. Computation Speed Score (0 - 100 pts)
    # SP is algorithm-level and not measured per sub-tier, so it never
    # participates in sub-tier scoring.  No data => None (NOT a neutral 75).
    s_spd: Optional[float] = None
    if not is_subtier:
        s_spd = _dimension_score(speed, "speed_score", "evaluated_count")
        if s_spd is not None:
            # Speed is uncapped by design; cap the discount input so it never
            # turns into a bonus.
            s_spd = min(max(s_spd, 0.0), 100.0)

    # 6. Anti-Jitter Score (0 - 100 pts)
    s_jit: Optional[float] = None
    if jitter is not None and int(getattr(jitter, "num_evaluated_maps", 0) or 0) > 0:
        s_jit = jitter_score(
            jitter.mape,
            arrangement_sensitivity_pct=getattr(jitter, "arrangement_sensitivity_pct", None),
            rstar_median=getattr(jitter, "rstar_median", None),
            hardening_share=getattr(jitter, "hardening_share", 0.0),
        )
        s_jit = max(0.0, min(100.0, s_jit))

    # Composite Score: ACC backbone x multiplicative robustness discounts.
    # Each dimension value is rounded to 2 dp *before* multiplication so the
    # published dimension cells can reproduce the total exactly.
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
        score_2dp = _finite(dim_score)
        if score_2dp is None:
            discounts[name] = None
            continue
        score_2dp = round(min(max(score_2dp, 0.0), 100.0), 2)
        discount = 1.0 - beta * (1.0 - score_2dp / 100.0)
        discounts[name] = round(discount, 6)
        total *= discount
    total = max(0.0, min(100.0, total))

    dim_scores: Dict[str, Optional[float]] = {
        "Ground-Truth Accuracy": round(s_acc, 2),
        "Anti-Short LN": round(s_sln, 2) if s_sln is not None else None,
        "Computation Speed": round(s_spd, 2) if s_spd is not None else None,
        "Anti-Jitter": round(s_jit, 2) if s_jit is not None else None,
        "Anti-Rate-Inversion": round(s_inv, 2) if s_inv is not None else None,
        "Local Continuity": round(s_cont, 2) if s_cont is not None else None,
    }

    return ScoreBreakdown(
        s_accuracy=round(s_acc, 2),
        s_inversion=round(s_inv, 2) if s_inv is not None else None,
        s_short_ln=round(s_sln, 2) if s_sln is not None else None,
        s_continuity=round(s_cont, 2) if s_cont is not None else None,
        s_speed=round(s_spd, 2) if s_spd is not None else None,
        s_jitter=round(s_jit, 2) if s_jit is not None else None,
        total_score=round(total, 2),
        dimension_scores=dim_scores,
        dimension_betas=weights,
        dimension_discounts=discounts,
        is_subtier=is_subtier,
        is_out_of_domain=False,
        is_evaluated=True,
        macro_severity=round(float(macro_severity), 6),
        macro_penalty=round(float(macro_penalty), 2),
    )
