"""Dimension 1: Ground-Truth Accuracy & Calibrated ng-RMSE Evaluator."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence
import numpy as np
from scipy import stats

from cab.metrics.calibration import CalibrationResult, ScaleCalibrator
from cab.parser.bench_loader import BenchmarkSample


@dataclass
class AccuracyResult:
    global_rmse: float
    global_mae: float
    spearman_rho: float
    spearman_p: float
    kendall_tau: float
    stratified_rmse: Dict[str, float]
    weighted_stratified_rmse: float
    mean_wtd: float
    wtd_by_tier: Dict[float, float]
    calibration: CalibrationResult
    calibrated_predictions: List[float] = field(default_factory=list)
    # Per-pattern sample counts, populated alongside ``stratified_rmse``.
    # Needed by the domain-profiling report so that a two-map pattern cannot be
    # mistaken for a genuine strength or weakness.
    stratified_counts: Dict[str, int] = field(default_factory=dict)


def compute_within_tier_dispersion(
    expected_ng: np.ndarray,
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
        mask = expected_ng == t
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
    """Evaluate accuracy dimension across all benchmark samples (including course)."""
    if len(samples) == 0:
        from cab.metrics.calibration import CalibrationResult
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
            calibration=CalibrationResult(is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[]),
            calibrated_predictions=[],
        )

    expected_arr = np.array([s.expected for s in samples], dtype=float)
    predicted_arr = np.array(raw_predictions, dtype=float)
    patterns = [s.pattern.lower() for s in samples]

    is_ng = scale_type.lower() == "ng"
    calibrator = ScaleCalibrator()
    calib_res = calibrator.calibrate(
        expected_arr, predicted_arr, is_ng_native=is_ng, scale_type=scale_type
    )

    # Obtain calibrated predictions in ng space
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
    pattern_weights = {
        "jack": 0.20,
        "speed": 0.20,
        "stamina": 0.20,
        "tech": 0.20,
        "ln": 0.15,
        "course": 0.05,
    }

    weighted_rmse_sum = 0.0
    total_w = 0.0

    for pat in unique_patterns:
        idx_pat = [i for i, p in enumerate(patterns) if p == pat]
        if idx_pat:
            diff_pat = diff[idx_pat]
            p_rmse = float(np.sqrt(np.mean(diff_pat ** 2)))
            stratified_rmse[pat] = p_rmse
            stratified_counts[pat] = len(idx_pat)

            w = pattern_weights.get(pat, 0.1)
            weighted_rmse_sum += w * p_rmse
            total_w += w

    weighted_rmse = weighted_rmse_sum / total_w if total_w > 0 else rmse

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
        weighted_stratified_rmse=weighted_rmse,
        mean_wtd=mean_wtd,
        wtd_by_tier=wtd_dict,
        calibration=calib_res,
        calibrated_predictions=list(calib_preds),
        stratified_counts=stratified_counts,
    )
