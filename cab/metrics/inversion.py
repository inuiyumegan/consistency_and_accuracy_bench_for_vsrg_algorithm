"""Dimension 2: Anti-Rate-Inversion Evaluator.

Assesses monotonicity under rate scaling (r in [1.01, 2.00]).
Measures Inversion Frequency (IF), Normalized Inversion Deficit AUC (IDA),
and Composite Inversion Index (CII).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple
import numpy as np


@dataclass
class MapInversionSummary:
    map_name: str
    steps_total: int
    steps_inverted: int
    inversion_ratio: float
    auc_deficit: float
    max_relative_drop: float
    worst_transition: Tuple[float, float, float, float]  # (r_k, r_k1, d_k, d_k1)


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
) -> InversionResult:
    """Evaluate rate inversion metrics across evaluated beatmaps.

    Args:
        rate_curves: Mapping from map_name to sequence of (clock_rate, difficulty).
                     Must be sorted by clock_rate in ascending order.
    """
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

        # Consecutive pairs
        r_prev = rates[:-1]
        r_curr = rates[1:]
        d_prev = diffs[:-1]
        d_curr = diffs[1:]

        # Drop is positive when difficulty decreases on speedup
        drops = d_prev - d_curr
        rel_drops = np.where(d_prev > 1e-6, drops / d_prev, 0.0)

        is_inverted = drops > 1e-6
        inv_count = int(np.sum(is_inverted))
        inverted_steps += inv_count

        delta_r = r_curr - r_prev
        auc_deficit_m = float(np.sum(np.maximum(0.0, rel_drops) * delta_r))
        total_auc_deficit += auc_deficit_m

        max_rel_m = float(np.max(rel_drops)) if len(rel_drops) > 0 else 0.0
        max_abs_m = float(np.max(drops)) if len(drops) > 0 else 0.0

        if max_rel_m > global_max_rel_drop:
            global_max_rel_drop = max_rel_m
        if max_abs_m > global_max_abs_drop:
            global_max_abs_drop = max_abs_m

        worst_idx = int(np.argmax(rel_drops)) if len(rel_drops) > 0 else 0
        worst_trans = (
            float(r_prev[worst_idx]),
            float(r_curr[worst_idx]),
            float(d_prev[worst_idx]),
            float(d_curr[worst_idx]),
        )

        if inv_count > 0:
            maps_with_inversion += 1

        map_summaries.append(
            MapInversionSummary(
                map_name=map_name,
                steps_total=steps,
                steps_inverted=inv_count,
                inversion_ratio=inv_count / steps if steps > 0 else 0.0,
                auc_deficit=auc_deficit_m,
                max_relative_drop=max_rel_m,
                worst_transition=worst_trans,
            )
        )

    num_maps = len(rate_curves)
    if_rate = inverted_steps / total_steps if total_steps > 0 else 0.0
    mean_auc = total_auc_deficit / num_maps if num_maps > 0 else 0.0

    # Composite Inversion Index (CII)
    # Balanced blend of IF (0.4), mean AUC deficit (0.3), and max relative drop (0.3)
    cii = float(0.4 * if_rate + 0.3 * min(1.0, mean_auc * 5.0) + 0.3 * min(1.0, global_max_rel_drop))

    # Sort map summaries by worst inversion
    map_summaries.sort(key=lambda s: (s.auc_deficit, s.inversion_ratio), reverse=True)

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
