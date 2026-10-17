"""Dimension 2: Anti-Rate-Inversion Evaluator.

Assesses monotonicity under rate scaling (r in [1.01, 2.00]).
Measures Inversion Frequency (IF), Normalized Inversion Deficit AUC (IDA),
and Composite Inversion Index (CII).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple
import numpy as np


DEADBAND_REL: float = 1e-4  # 0.01% relative drawdown deadband
DEADBAND_ABS: float = 5e-4  # 0.0005 rating unit absolute drawdown deadband


@dataclass
class MapInversionSummary:
    map_name: str
    steps_total: int
    steps_inverted: int
    inversion_ratio: float
    auc_deficit: float
    max_relative_drop: float
    worst_transition: Tuple[float, float, float, float]  # (r_peak, r_trough, d_peak, d_trough)


@dataclass
class InversionResult:
    inversion_frequency: float
    auc_deficit: float
    max_relative_drop: float
    max_absolute_drop: float
    inverted_maps_count: int
    total_maps_evaluated: int
    composite_inversion_index: float
    worst_maps: List[MapInversionSummary] = field(default_factory=list)


def evaluate_rate_inversion(
    rate_curves: Dict[str, Sequence[Tuple[float, float]]],
    deadband_rel: float = DEADBAND_REL,
    deadband_abs: float = DEADBAND_ABS,
) -> InversionResult:
    """Evaluate rate inversion metrics across evaluated beatmaps.

    Uses:
    1. Rate-span weighting (delta_r) for Inversion Frequency (IF), so adaptive
       Phase-2 refinement does not inflate IF by oversampling suspicious intervals.
    2. Running-maximum envelope drawdown M(r) - D(r) integrated via the normalized
       trapezoidal rule for IDA (auc_deficit) and peak-to-trough max drops, making
       both IDA and max_relative_drop invariant to mesh step size.
    3. Shift-safe denominator max(M(r) - min(0, d_min), 1.0) so negative or
       near-zero scales never divide by zero or explode.
    4. Micro-deadband tolerance (deadband_rel, deadband_abs) to ignore 1ms
       integer timestamp rounding noise while preserving genuine inversions.

    Args:
        rate_curves: Mapping from map_name to sequence of (clock_rate, difficulty).
                     Must be sorted by clock_rate in ascending order.
        deadband_rel: Minimum relative drawdown to count as a genuine inversion.
        deadband_abs: Minimum absolute drawdown to count as a genuine inversion.
    """
    total_rate_span = 0.0
    inverted_rate_span = 0.0
    total_steps = 0
    inverted_steps = 0
    total_auc_deficit = 0.0
    global_max_rel_drop = 0.0
    global_max_abs_drop = 0.0

    map_summaries: List[MapInversionSummary] = []
    maps_with_inversion = 0

    for map_name, curve in rate_curves.items():
        if len(curve) < 2:
            continue

        rates = np.array([pt[0] for pt in curve], dtype=float)
        diffs = np.array([pt[1] for pt in curve], dtype=float)

        steps = len(rates) - 1
        total_steps += steps

        # Consecutive pairs and step widths
        r_prev = rates[:-1]
        r_curr = rates[1:]
        d_prev = diffs[:-1]
        d_curr = diffs[1:]
        delta_r = np.maximum(r_curr - r_prev, 0.0)
        map_span_r = float(np.sum(delta_r))
        total_rate_span += map_span_r

        # Running peak envelope M_i = max_{j <= i} d_j and index of the active peak
        running_max = np.empty_like(diffs)
        peak_idx = np.empty(len(diffs), dtype=int)
        cur_max = diffs[0]
        cur_idx = 0
        for i, val in enumerate(diffs):
            if val >= cur_max:
                cur_max = val
                cur_idx = i
            running_max[i] = cur_max
            peak_idx[i] = cur_idx

        # Shift-safe denominator: preserves M_i for positive scales >= 1.0,
        # floors near-zero positive values at 1.0, and shifts negative scales
        # (e.g. ND Beginner maps in [-2, 0]) relative to d_min.
        d_min = float(np.min(diffs))
        floor_shift = min(0.0, d_min)
        denom_nodes = np.maximum(running_max - floor_shift, 1.0)
        denom_steps = denom_nodes[:-1]

        # Envelope drawdown from running peak at each node
        abs_drawdowns = running_max - diffs
        rel_drawdowns = abs_drawdowns / denom_nodes
        valid_dd = (abs_drawdowns > deadband_abs) & (rel_drawdowns > deadband_rel)
        abs_drawdowns = np.where(valid_dd, abs_drawdowns, 0.0)
        rel_drawdowns = np.where(valid_dd, rel_drawdowns, 0.0)

        # Step-level drop with step-size-scaled deadband + envelope confirmation
        step_drops = d_prev - d_curr
        step_rel_drops = step_drops / denom_steps
        step_scale = np.clip(delta_r / 0.10, 0.2, 1.0)
        is_inverted = (
            (step_drops > deadband_abs * step_scale)
            & (step_rel_drops > deadband_rel * step_scale)
            & valid_dd[1:]
        )

        inv_count = int(np.sum(is_inverted))
        inverted_steps += inv_count
        inv_span_m = float(np.sum(delta_r[is_inverted]))
        inverted_rate_span += inv_span_m

        inv_ratio_m = (
            inv_span_m / map_span_r
            if map_span_r > 0.0
            else (inv_count / steps if steps > 0 else 0.0)
        )

        # Normalized trapezoidal integral of relative drawdown over [r_min, r_max]
        if map_span_r > 0.0:
            trapz_area = float(np.sum(0.5 * (rel_drawdowns[:-1] + rel_drawdowns[1:]) * delta_r))
            auc_deficit_m = trapz_area / map_span_r
        else:
            auc_deficit_m = 0.0
        total_auc_deficit += auc_deficit_m

        max_rel_m = float(np.max(rel_drawdowns)) if len(rel_drawdowns) > 0 else 0.0
        max_abs_m = float(np.max(abs_drawdowns)) if len(abs_drawdowns) > 0 else 0.0

        if max_rel_m > global_max_rel_drop:
            global_max_rel_drop = max_rel_m
        if max_abs_m > global_max_abs_drop:
            global_max_abs_drop = max_abs_m

        if max_rel_m > 0.0 and len(rel_drawdowns) > 0:
            worst_i = int(np.argmax(rel_drawdowns))
            p_i = int(peak_idx[worst_i])
            worst_trans = (
                float(rates[p_i]),
                float(rates[worst_i]),
                float(diffs[p_i]),
                float(diffs[worst_i]),
            )
        else:
            worst_trans = (
                float(rates[0]),
                float(rates[-1]),
                float(diffs[0]),
                float(diffs[-1]),
            )

        if inv_count > 0:
            maps_with_inversion += 1

        map_summaries.append(
            MapInversionSummary(
                map_name=map_name,
                steps_total=steps,
                steps_inverted=inv_count,
                inversion_ratio=inv_ratio_m,
                auc_deficit=auc_deficit_m,
                max_relative_drop=max_rel_m,
                worst_transition=worst_trans,
            )
        )

    num_maps = len(rate_curves)
    if total_rate_span > 0.0:
        if_rate = inverted_rate_span / total_rate_span
    else:
        if_rate = inverted_steps / total_steps if total_steps > 0 else 0.0
    mean_auc = total_auc_deficit / num_maps if num_maps > 0 else 0.0

    # Composite Inversion Index (CII)
    # Balanced blend of IF (0.4), mean AUC deficit (0.3), and max relative drop (0.3)
    cii = float(0.4 * if_rate + 0.3 * min(1.0, mean_auc * 5.0) + 0.3 * min(1.0, global_max_rel_drop))

    # Sort map summaries by worst inversion
    map_summaries.sort(key=lambda s: (s.auc_deficit, s.inversion_ratio, s.max_relative_drop), reverse=True)

    return InversionResult(
        inversion_frequency=if_rate,
        auc_deficit=mean_auc,
        max_relative_drop=global_max_rel_drop,
        max_absolute_drop=global_max_abs_drop,
        inverted_maps_count=maps_with_inversion,
        total_maps_evaluated=num_maps,
        composite_inversion_index=cii,
        worst_maps=map_summaries[:10],
    )

