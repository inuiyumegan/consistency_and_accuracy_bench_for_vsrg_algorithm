"""Cohort Centroid Anchoring and Weighted Isotonic (PAVA) Calibration Engine.

Aligns non-ND scale algorithms (e.g. Star-Rating-Rebirth, Daniel) to the empirical ND scale,
detects macro tier inversions, applies sample-weighted isotonic pooling, and constructs
a strictly monotonic calibration mapping f_calib(y) -> ND.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import numpy as np
from scipy.interpolate import PchipInterpolator


@dataclass
class MacroInversionAlert:
    lower_tier: float
    higher_tier: float
    lower_anchor: float
    higher_anchor: float
    violation_amount: float

    def __str__(self) -> str:
        return (
            f"[Macro Inversion] ND {self.lower_tier:.1f} (Anchor {self.lower_anchor:.4f}) "
            f"> ND {self.higher_tier:.1f} (Anchor {self.higher_anchor:.4f}) | "
            f"Deficit: +{self.violation_amount:.4f}"
        )


def weighted_pava(y: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Weighted Pool Adjacent Violators Algorithm (PAVA).
    
    Solves: min sum w_i * (y_i - y_hat_i)^2 subject to y_hat_1 <= y_hat_2 <= ... <= y_hat_n.
    Guarantees strict monotonicity without inverse fold-back.
    """
    n = len(y)
    if n <= 1:
        return y.copy()

    # Block representation: (start_index, end_index, weight, weighted_sum)
    blocks: List[List[float]] = []  # [start, end, w, w_sum]
    for i in range(n):
        blocks.append([i, i, float(weights[i]), float(weights[i] * y[i])])

    i = 0
    while i < len(blocks) - 1:
        val_curr = blocks[i][3] / blocks[i][2]
        val_next = blocks[i + 1][3] / blocks[i + 1][2]
        if val_curr > val_next:
            # Violation: pool adjacent blocks
            blocks[i][1] = blocks[i + 1][1]
            blocks[i][2] += blocks[i + 1][2]
            blocks[i][3] += blocks[i + 1][3]
            blocks.pop(i + 1)
            # Check backwards
            if i > 0:
                i -= 1
        else:
            i += 1

    result = np.zeros(n, dtype=float)
    for b in blocks:
        start_idx = int(b[0])
        end_idx = int(b[1])
        avg = b[3] / b[2]
        result[start_idx : end_idx + 1] = avg

    return result


@dataclass
class CalibrationResult:
    is_nd: bool
    raw_anchors: Dict[float, float]
    pava_anchors: Dict[float, float]
    macro_alerts: List[MacroInversionAlert]
    calibrator_fn: Optional[PchipInterpolator] = None
    conversion_formula: Optional[str] = None
    min_algo_val: float = 0.0
    max_algo_val: float = 0.0
    min_nd: float = 0.0
    max_nd: float = 0.0
    #: Tier anchors actually used for the curve.
    anchors_used: int = 0
    #: Tiers skipped because they had fewer than ``min_cohort_size`` samples.
    tiers_skipped_small: int = 0

    def predict(self, algo_values: np.ndarray) -> np.ndarray:
        """Map algorithm predictions to the calibrated ND scale."""
        arr = np.asarray(algo_values, dtype=float)
        if self.is_nd:
            return arr

        # Direct explicit conversion standard: daniel - 0.5 = ND
        if self.conversion_formula == "daniel":
            return arr - 0.5

        if self.calibrator_fn is None:
            return arr

        # For values within domain, use PCHIP interpolator
        # For values outside domain, use linear extrapolation with boundary slopes
        # ``np.empty_like`` used to leave non-finite inputs uninitialised, so a
        # NaN prediction mapped to whatever garbage was in memory.  Start from
        # NaN and only fill the masks we can actually evaluate.
        res = np.full(arr.shape, np.nan, dtype=float)
        finite = np.isfinite(arr)
        in_mask = finite & (arr >= self.min_algo_val) & (arr <= self.max_algo_val)
        if np.any(in_mask):
            res[in_mask] = self.calibrator_fn(arr[in_mask])

        # Extrapolate left
        low_mask = finite & (arr < self.min_algo_val)
        if np.any(low_mask):
            slope_low = float(self.calibrator_fn.derivative(1)(self.min_algo_val))
            slope_low = max(slope_low, 0.1)  # keep positive slope
            res[low_mask] = self.min_nd + slope_low * (arr[low_mask] - self.min_algo_val)

        # Extrapolate right
        high_mask = finite & (arr > self.max_algo_val)
        if np.any(high_mask):
            slope_high = float(self.calibrator_fn.derivative(1)(self.max_algo_val))
            slope_high = max(slope_high, 0.1)
            res[high_mask] = self.max_nd + slope_high * (arr[high_mask] - self.max_algo_val)

        return res


class ScaleCalibrator:
    """Cohort Centroid Anchoring and Monotonic Cross-Scale Calibrator."""

    def __init__(self, min_cohort_size: int = 3):
        self.min_cohort_size = min_cohort_size

    def calibrate(
        self,
        expected_nd: np.ndarray,
        predicted_algo: np.ndarray,
        is_nd_native: bool = False,
        scale_type: str = "auto",
    ) -> CalibrationResult:
        """Fit calibration curve mapping predicted_algo to expected_nd.
        
        Args:
            expected_nd: Ground truth ND values.
            predicted_algo: Target algorithm raw predicted difficulties.
            is_nd_native: If True, algorithm natively operates on the ND scale.
            scale_type: 'nd', 'daniel' (daniel - 0.5 = ND), or 'non-nd' / 'auto'.
        """
        expected = np.asarray(expected_nd, dtype=float)
        predicted = np.asarray(predicted_algo, dtype=float)

        # 1. Group by tier nd_k
        unique_tiers = np.unique(expected)
        unique_tiers.sort()

        tier_anchors: List[float] = []
        tier_weights: List[float] = []
        valid_tiers: List[float] = []

        def _collect(min_count: int):
            anchors: List[float] = []
            weights: List[float] = []
            tiers: List[float] = []
            skipped = 0
            for t in unique_tiers:
                mask = expected == t
                vals = predicted[mask]
                count = len(vals)
                if count == 0:
                    continue
                if count < min_count:
                    skipped += 1
                    continue
                finite_vals = vals[np.isfinite(vals)]
                if len(finite_vals) == 0:
                    skipped += 1
                    continue
                # Robust cohort centroid: median
                anchors.append(float(np.median(finite_vals)))
                tiers.append(float(t))
                weights.append(float(np.sqrt(len(finite_vals))))
            return tiers, anchors, weights, skipped

        # ``min_cohort_size`` used to be dead: a tier with a single map became a
        # calibration anchor and could drive the PCHIP curve and the macro
        # inversion penalty.  Apply it, but fall back to every tier when a
        # narrow slice would leave fewer than two anchors to interpolate.
        valid_tiers, tier_anchors, tier_weights, skipped_small = _collect(
            max(1, int(self.min_cohort_size))
        )
        if len(valid_tiers) < 2:
            valid_tiers, tier_anchors, tier_weights, skipped_small = _collect(1)

        raw_anchors_dict: Dict[float, float] = {
            t: a for t, a in zip(valid_tiers, tier_anchors)
        }

        valid_tiers_arr = np.array(valid_tiers, dtype=float)
        anchors_arr = np.array(tier_anchors, dtype=float)
        weights_arr = np.array(tier_weights, dtype=float)

        # 2. Macro Monotonicity Check
        macro_alerts: List[MacroInversionAlert] = []
        for i in range(len(valid_tiers_arr) - 1):
            if anchors_arr[i] > anchors_arr[i + 1]:
                macro_alerts.append(
                    MacroInversionAlert(
                        lower_tier=valid_tiers_arr[i],
                        higher_tier=valid_tiers_arr[i + 1],
                        lower_anchor=anchors_arr[i],
                        higher_anchor=anchors_arr[i + 1],
                        violation_amount=anchors_arr[i] - anchors_arr[i + 1],
                    )
                )

        # nd-native algorithms keep an identity mapping: no PAVA smoothing and
        # no interpolation curve are constructed.  The cohort anchors and the
        # macro-monotonicity diagnostics above are nevertheless recorded — the
        # structural-inversion checks (and the ACC penalty derived from them)
        # must cover these algorithms too, not only the calibrated ones.
        if is_nd_native or scale_type.lower() == "nd":
            return CalibrationResult(
                is_nd=True,
                raw_anchors=raw_anchors_dict,
                pava_anchors=dict(raw_anchors_dict),
                macro_alerts=macro_alerts,
            )

        # 3. Weighted PAVA Smoothing on Anchor values
        # Note: We want anchor values to be monotonically non-decreasing with respect to ND tiers
        smoothed_anchors = weighted_pava(anchors_arr, weights_arr)

        pava_anchors_dict = {
            float(t): float(sa) for t, sa in zip(valid_tiers_arr, smoothed_anchors)
        }

        # 4. Construct strictly monotonic calibration function f(algo_val) -> ND
        # Compress flat tiers resulting from PAVA to ensure strictly increasing x for interpolation
        unique_x: List[float] = []
        unique_y: List[float] = []

        curr_x = None
        curr_y_vals = []

        for x_val, y_val in zip(smoothed_anchors, valid_tiers_arr):
            if curr_x is None:
                curr_x = x_val
                curr_y_vals = [y_val]
            elif np.isclose(x_val, curr_x, atol=1e-7):
                curr_y_vals.append(y_val)
            else:
                unique_x.append(curr_x)
                unique_y.append(float(np.mean(curr_y_vals)))
                curr_x = x_val
                curr_y_vals = [y_val]

        if curr_x is not None:
            unique_x.append(curr_x)
            unique_y.append(float(np.mean(curr_y_vals)))

        # Ensure strictly increasing
        for k in range(1, len(unique_x)):
            if unique_x[k] <= unique_x[k - 1]:
                unique_x[k] = unique_x[k - 1] + 1e-5

        x_pts = np.array(unique_x, dtype=float)
        y_pts = np.array(unique_y, dtype=float)

        if len(x_pts) >= 2:
            pchip = PchipInterpolator(x_pts, y_pts)
        else:
            pchip = None

        return CalibrationResult(
            is_nd=False,
            raw_anchors=raw_anchors_dict,
            pava_anchors=pava_anchors_dict,
            macro_alerts=macro_alerts,
            calibrator_fn=pchip,
            conversion_formula="daniel" if scale_type.lower() == "daniel" else None,
            min_algo_val=float(x_pts[0]) if len(x_pts) > 0 else 0.0,
            max_algo_val=float(x_pts[-1]) if len(x_pts) > 0 else 1.0,
            min_nd=float(y_pts[0]) if len(y_pts) > 0 else 0.0,
            max_nd=float(y_pts[-1]) if len(y_pts) > 0 else 1.0,
            anchors_used=len(valid_tiers),
            tiers_skipped_small=skipped_small,
        )
