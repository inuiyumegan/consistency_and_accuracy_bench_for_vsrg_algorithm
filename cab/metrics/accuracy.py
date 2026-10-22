"""Dimension 1: Ground-Truth Accuracy & Calibrated ND-RMSE Evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence
import numpy as np
from scipy import stats

from cab.metrics.calibration import CalibrationResult, ScaleCalibrator
from cab.parser.bench_loader import BenchmarkSample


# Per-pattern (family) weights.  These are *fixed* and sample-count independent,
# so no pattern can dominate the score merely because the sample library happens
# to hold more of it.
#
# ``unsigned`` is the catch-all for unclassified patterns; it holds only a
# dozen beginner-tier singles, so it is pinned at the ``course`` level.  The
# previous implicit fallback was 0.1 -- twice ``course`` -- which let a 1.5%
# bucket carry 34.5% of one algorithm's balanced score.
PATTERN_WEIGHTS: Dict[str, float] = {
    "jack": 0.20,
    "speed": 0.20,
    "stamina": 0.20,
    "tech": 0.20,
    "ln": 0.15,
    "course": 0.05,
    "unsigned": 0.05,
}

# Weight applied to any pattern not enumerated above, so a future unlisted
# bucket can never dominate the balanced RMSE.
DEFAULT_PATTERN_WEIGHT: float = 0.05


# ---------------------------------------------------------------------------
# Sub-pattern weighting (2026-10-18)
# ---------------------------------------------------------------------------
# The family weight above is preserved; inside a family it is split across the
# present sub-patterns by a fixed ratio: every *classified* sub-pattern counts
# as 3, the ``Unsigned`` catch-all counts as 1 -- i.e. "已分类 : unsigned = 3 : 1"
# (e.g. tech: stream tech / jack tech / mix tech : Unsigned = 3 : 3 : 3 : 1).
#
# Sub-patterns are identified as ``family-subpattern`` (binomial naming); the
# family comes from the sample's ``pattern`` field, so the label itself only
# needs the sub-pattern part.  Ratios are re-normalised over the sub-patterns
# that are actually present, which keeps the family's total weight intact even
# when a future dataset drops one of them.
SUB_PATTERN_CLASSIFIED_RATIO: float = 3.0
SUB_PATTERN_UNSIGNED_RATIO: float = 1.0
UNSIGNED_SUB_PATTERN: str = "Unsigned"

# Recording typo: ``jacky tech`` is the same bucket as ``jack tech``.
SUB_PATTERN_ALIASES: Dict[str, str] = {"jacky tech": "jack tech"}


def normalize_sub_pattern(name: str) -> str:
    """Return the canonical sub-pattern label (alias merge + strip)."""
    key = (name or "").strip()
    return SUB_PATTERN_ALIASES.get(key, key)


def sub_pattern_ratio(name: str) -> float:
    """Ratio of a sub-pattern inside its family (3 classified, 1 unsigned)."""
    return (
        SUB_PATTERN_UNSIGNED_RATIO
        if normalize_sub_pattern(name) == UNSIGNED_SUB_PATTERN
        else SUB_PATTERN_CLASSIFIED_RATIO
    )


def binomial_sub_pattern(family: str, sub_pattern: str) -> str:
    """Canonical ``family-subpattern`` id used by reports and JSON payloads."""
    return f"{family.lower()}-{normalize_sub_pattern(sub_pattern)}"


def balanced_stratified_rmse(
    stratified_rmse: Dict[str, float],
    stratified_counts: Dict[str, int] | None = None,
) -> float | None:
    """Aggregate per-pattern RMSEs into the *family-level* balanced RMSE.

    Kept as the diagnostic counterpart of :func:`balanced_subpattern_rmse`
    (the scoring value).  This is a **weight-normalised arithmetic mean** of
    the per-pattern RMSEs (``sum(w_p * rmse_p) / sum(w_p)``), *not* a weighted
    root-mean-square.  ``stratified_counts`` is accepted for signature symmetry
    with the JSON payloads but is deliberately unused: the whole point of this
    metric is that it does not depend on how many maps each pattern has.

    Returns ``None`` when there is nothing to aggregate.
    """
    num = 0.0
    den = 0.0
    for pat, rmse in (stratified_rmse or {}).items():
        if rmse is None:
            continue
        w = PATTERN_WEIGHTS.get(pat, DEFAULT_PATTERN_WEIGHT)
        num += w * float(rmse)
        den += w
    if den <= 0:
        return None
    return num / den


def balanced_subpattern_rmse(
    stratified_subpattern_rmse: Dict[str, Dict[str, float]],
    stratified_subpattern_counts: Dict[str, Dict[str, int]] | None = None,
) -> float | None:
    """Aggregate per-sub-pattern RMSEs into the *scoring* balanced RMSE.

    Family weights (``PATTERN_WEIGHTS``) are preserved.  Inside each family the
    weight is divided among the present sub-patterns by
    :func:`sub_pattern_ratio` (3 for classified buckets, 1 for ``Unsigned``),
    then re-normalised so the family still contributes exactly its own weight.

    Like the family-level counterpart this is a weight-normalised arithmetic
    mean of RMSEs, not a weighted root-mean-square; ``stratified_subpattern_counts``
    is accepted for signature symmetry but unused (weights are fixed and
    sample-count independent by design).

    Returns ``None`` when there is nothing to aggregate.
    """
    if not stratified_subpattern_rmse:
        return None

    num = 0.0
    den = 0.0
    for family, subs in stratified_subpattern_rmse.items():
        if not subs:
            continue
        fam_w = PATTERN_WEIGHTS.get(family, DEFAULT_PATTERN_WEIGHT)
        ratios: Dict[str, float] = {}
        for sub, rmse in subs.items():
            if rmse is None:
                continue
            ratios[sub] = sub_pattern_ratio(sub)
        total_ratio = sum(ratios.values())
        if total_ratio <= 0:
            continue
        den += fam_w
        for sub, ratio in ratios.items():
            num += fam_w * (ratio / total_ratio) * float(subs[sub])
    if den <= 0:
        return None
    return num / den


@dataclass
class AccuracyResult:
    global_rmse: float
    global_mae: float
    spearman_rho: float
    spearman_p: float
    kendall_tau: float
    stratified_rmse: Dict[str, float]
    #: Scoring value: sub-pattern balanced (family weights preserved, 3:1 split).
    weighted_stratified_rmse: float
    mean_wtd: float
    wtd_by_tier: Dict[float, float]
    calibration: CalibrationResult
    calibrated_predictions: List[float] = field(default_factory=list)
    # Per-pattern sample counts, populated alongside ``stratified_rmse``.
    # Needed by the domain-profiling report so that a two-map pattern cannot be
    # mistaken for a genuine strength or weakness.
    stratified_counts: Dict[str, int] = field(default_factory=dict)
    # Diagnostic: family-level balanced RMSE (pre-2026-10-18 scoring value).
    weighted_family_rmse: float = 0.0
    # Per-sub-pattern breakdown: ``{family: {sub_pattern: rmse}}`` (+ counts).
    stratified_subpattern_rmse: Dict[str, Dict[str, float]] = field(default_factory=dict)
    stratified_subpattern_counts: Dict[str, Dict[str, int]] = field(default_factory=dict)
    #: Number of samples that actually entered the statistics.  ``0`` means the
    #: accuracy backbone was not evaluated at all -- scoring must then report
    #: N/A instead of awarding the 40-point "zero RMSE" floor.  ``None`` means
    #: "not recorded" (hand-built results, e.g. unit-test fixtures) and is
    #: treated as evaluated.
    num_samples: Optional[int] = None
    #: Samples dropped because their prediction (or truth) was NaN/inf.
    num_rejected_nonfinite: int = 0


def compute_within_tier_dispersion(
    expected_nd: np.ndarray,
    predicted_algo: np.ndarray,
    anchors: Dict[float, float],
) -> tuple[float, Dict[float, float]]:
    """Compute Within-Tier Dispersion (WTD) measuring intra-tier cohesion.
    
    WTD_k = sigma_k / (Delta_Anchor_k + eps)
    """
    sorted_tiers = sorted(anchors.keys())
    wtd_dict: Dict[float, float] = {}

    anchor_vals = [anchors[k] for k in sorted_tiers]
    distinct_vals = sorted(set(anchor_vals))
    if len(distinct_vals) >= 2:
        global_step = (distinct_vals[-1] - distinct_vals[0]) / (len(distinct_vals) - 1)
    else:
        global_step = 1.0

    for idx, t in enumerate(sorted_tiers):
        mask = expected_nd == t
        vals = predicted_algo[mask]
        if len(vals) < 2:
            continue

        sigma = float(np.std(vals, ddof=1))

        curr_a = anchors[t]
        lower_diffs = [curr_a - a for a in anchor_vals if a < curr_a - 1e-5]
        higher_diffs = [a - curr_a for a in anchor_vals if a > curr_a + 1e-5]

        steps: List[float] = []
        if lower_diffs:
            steps.append(min(lower_diffs))
        if higher_diffs:
            steps.append(min(higher_diffs))

        delta_anchor = np.mean(steps) if steps else global_step
        if delta_anchor < 1e-4:
            delta_anchor = global_step

        wtd = sigma / delta_anchor
        wtd_dict[float(t)] = float(wtd)

    mean_wtd = float(np.mean(list(wtd_dict.values()))) if wtd_dict else 0.0
    return mean_wtd, wtd_dict


def evaluate_accuracy(
    samples: Sequence[BenchmarkSample],
    raw_predictions: Sequence[float],
    scale_type: str = "auto",
) -> AccuracyResult:
    """Evaluate accuracy dimension across all benchmark samples (including course).

    Predictions that are not finite are **rejected**, not fed into the RMSE:
    ``max(0.0, nan - 0.3)`` silently evaluates to ``0.0`` and used to award the
    full 40-point RMSE sub-score, while the PCHIP calibrator raised on non-ND
    scales.  A result with zero usable samples carries ``num_samples=0`` so the
    scorer can report N/A.
    """
    from cab.metrics.calibration import CalibrationResult

    def _empty(num_rejected: int = 0) -> AccuracyResult:
        return AccuracyResult(
            global_rmse=0.0,
            global_mae=0.0,
            spearman_rho=0.0,
            spearman_p=1.0,
            kendall_tau=0.0,
            stratified_rmse={},
            weighted_stratified_rmse=0.0,
            mean_wtd=0.0,
            wtd_by_tier={},
            calibration=CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[]),
            calibrated_predictions=[],
            num_samples=0,
            num_rejected_nonfinite=num_rejected,
        )

    if len(samples) == 0:
        return _empty()

    if len(raw_predictions) != len(samples):
        raise ValueError(
            f"evaluate_accuracy: {len(samples)} samples but "
            f"{len(raw_predictions)} predictions; refusing to zip mismatched arrays"
        )

    expected_arr = np.array([s.expected for s in samples], dtype=float)
    predicted_arr = np.array(raw_predictions, dtype=float)

    finite_mask = np.isfinite(expected_arr) & np.isfinite(predicted_arr)
    rejected = int((~finite_mask).sum())
    if rejected:
        import warnings

        warnings.warn(
            f"evaluate_accuracy: rejected {rejected}/{len(samples)} non-finite "
            "prediction(s) instead of scoring them as perfect",
            RuntimeWarning,
            stacklevel=2,
        )
        expected_arr = expected_arr[finite_mask]
        predicted_arr = predicted_arr[finite_mask]
        samples = [s for s, keep in zip(samples, finite_mask) if keep]
        if len(samples) == 0:
            return _empty(rejected)

    patterns = [s.pattern.lower() for s in samples]

    is_nd = scale_type.lower() == "nd"
    calibrator = ScaleCalibrator()
    calib_res = calibrator.calibrate(
        expected_arr, predicted_arr, is_nd_native=is_nd, scale_type=scale_type
    )

    # Obtain calibrated predictions in ND space
    calib_preds = calib_res.predict(predicted_arr)

    # Global RMSE & MAE
    diff = calib_preds - expected_arr
    rmse = float(np.sqrt(np.mean(diff ** 2)))
    mae = float(np.mean(np.abs(diff)))

    s_res = stats.spearmanr(predicted_arr, expected_arr)
    k_res = stats.kendalltau(predicted_arr, expected_arr)
    rho = float(s_res.statistic) if hasattr(s_res, "statistic") else float(s_res[0])
    p_val = float(s_res.pvalue) if hasattr(s_res, "pvalue") else float(s_res[1])
    tau = float(k_res.statistic) if hasattr(k_res, "statistic") else float(k_res[0])
    if np.isnan(rho):
        rho = 0.0
    if np.isnan(p_val):
        p_val = 1.0
    if np.isnan(tau):
        tau = 0.0

    # Pattern-stratified RMSE
    unique_patterns = sorted(set(patterns))
    stratified_rmse: Dict[str, float] = {}
    stratified_counts: Dict[str, int] = {}

    for pat in unique_patterns:
        idx_pat = [i for i, p in enumerate(patterns) if p == pat]
        if idx_pat:
            diff_pat = diff[idx_pat]
            p_rmse = float(np.sqrt(np.mean(diff_pat ** 2)))
            stratified_rmse[pat] = p_rmse
            stratified_counts[pat] = len(idx_pat)

    family_weighted = balanced_stratified_rmse(stratified_rmse, stratified_counts)
    if family_weighted is None:
        family_weighted = rmse

    # Sub-pattern-stratified RMSE: the scoring granularity (2026-10-18).
    sub_stratified_rmse: Dict[str, Dict[str, float]] = {}
    sub_stratified_counts: Dict[str, Dict[str, int]] = {}
    sub_buckets: Dict[tuple, List[int]] = {}
    for i, s in enumerate(samples):
        fam = s.pattern.lower()
        sub = normalize_sub_pattern(getattr(s, "sub_pattern", "") or "")
        sub_buckets.setdefault((fam, sub), []).append(i)

    for (fam, sub), idx_sub in sub_buckets.items():
        diff_sub = diff[idx_sub]
        sub_stratified_rmse.setdefault(fam, {})[sub] = float(np.sqrt(np.mean(diff_sub ** 2)))
        sub_stratified_counts.setdefault(fam, {})[sub] = len(idx_sub)

    weighted_rmse = balanced_subpattern_rmse(sub_stratified_rmse, sub_stratified_counts)
    if weighted_rmse is None:
        weighted_rmse = family_weighted

    # Within-Tier Dispersion
    anchors_to_use = calib_res.raw_anchors if calib_res.raw_anchors else calib_res.pava_anchors
    mean_wtd, wtd_dict = compute_within_tier_dispersion(expected_arr, predicted_arr, anchors_to_use)

    return AccuracyResult(
        global_rmse=rmse,
        global_mae=mae,
        spearman_rho=rho,
        spearman_p=p_val,
        kendall_tau=tau,
        stratified_rmse=stratified_rmse,
        weighted_stratified_rmse=float(weighted_rmse),
        mean_wtd=mean_wtd,
        wtd_by_tier=wtd_dict,
        calibration=calib_res,
        calibrated_predictions=list(calib_preds),
        stratified_counts=stratified_counts,
        weighted_family_rmse=float(family_weighted),
        stratified_subpattern_rmse=sub_stratified_rmse,
        stratified_subpattern_counts=sub_stratified_counts,
        num_samples=int(len(samples)),
        num_rejected_nonfinite=rejected,
    )
