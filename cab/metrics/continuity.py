"""Dimension 3: Local Continuity & Cliffing Robustness Evaluator.

Detects discrete jumps, hard threshold boundaries, and local Lipschitz bounds
under micro-step rate variations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple
import math
import numpy as np


@dataclass
class CliffEvent:
    map_name: str
    rate_before: float
    rate_after: float
    diff_before: float
    diff_after: float
    local_gradient: float
    relative_jump: float

    def __str__(self) -> str:
        return (
            f"[{self.map_name}] Rate {self.rate_before:.3f} -> {self.rate_after:.3f} | "
            f"Diff: {self.diff_before:.3f} -> {self.diff_after:.3f} | "
            f"Jump: {self.relative_jump*100:+.1f}% | Grad: {self.local_gradient:.1f}"
        )


@dataclass
class ContinuityResult:
    cliff_events_count: int
    max_lipschitz: float
    mean_lipschitz: float
    smoothness_score: float  # [0, 100]
    cliff_events: List[CliffEvent] = field(default_factory=list)
    cliff_rate: float = 0.0
    mean_roughness: float = 0.0
    max_normalized_lipschitz: float = 0.0
    #: Maps with at least three usable (finite) rate points.  0 means the
    #: dimension was not evaluated and must be reported as N/A, not 100.
    #: ``None`` means "not recorded" (hand-built results, e.g. test fixtures)
    #: and is treated as evaluated.
    evaluated_maps: Optional[int] = None


def evaluate_continuity(
    rate_curves: Dict[str, Sequence[Tuple[float, float]]],
    z_threshold: float = 3.5,
    rel_jump_threshold: float = 0.06,
    min_rate: float = 1.0,
) -> ContinuityResult:
    """Evaluate sub-algorithm handoff continuity and cliffing events along rate curves.

    Specifically targets discrete regime-switching discontinuities (e.g. unblended
    routing handoffs between sub-algorithms or oversized decision-tree / LUT bucket
    split jumps >= 6% in a 0.02 rate step) while tolerating benign tree leaf steps
    and continuous nonlinear slope variations inherent to empirical scales like ND:
    1. Scale-invariant normalized slopes g_norm = (Delta d / Delta r) / scale_m,
       where scale_m = max(mean(d - min(0, d_min)), d_max - d_min, 1.0).
    2. Bilateral impulse detection: compares each step's slope g_norm[i] against
       both its pre-step neighborhood median g_left and post-step neighborhood
       median g_right. If a curve's slope is monotonically accelerating or
       decelerating into a steeper empirical ND region (g_left <= g_norm[i] <= g_right),
       its bilateral impulse is zero. Only an isolated step jump that shoots past
       BOTH g_left and g_right registers a positive impulse.
    3. Smoothness score is 100.0 when no cliff events occur, and decays according
       to the rate-span-weighted cliff rate (CR) and mean per-map excess handoff
       jump (MCD), independent of map count M.

    Args:
        rate_curves: Mapping from map_name to sequence of (clock_rate, difficulty).
        z_threshold: Robust Z-score threshold for bilateral impulse detection.
        rel_jump_threshold: Minimum 0.02-rate-equivalent relative jump for a cliff (default 6%).
        min_rate: Lowest clock rate admitted.  Sub-unity probe points exist for
            the r* inversion; continuity is a property of the speed-up domain,
            so they are excluded here (same rationale as AIN).
    """
    all_lipschitz: List[float] = []
    all_norm_lipschitz: List[float] = []
    map_roughnesses: List[float] = []
    cliff_events: List[CliffEvent] = []
    total_rate_span = 0.0
    cliff_rate_span = 0.0
    total_excess_jump = 0.0
    valid_maps = 0

    for map_name, curve in rate_curves.items():
        # A non-finite difficulty would poison every gradient downstream and
        # used to read as "perfectly smooth" (100 pts). Drop those points.
        pts = [
            pt
            for pt in curve
            if pt[0] >= min_rate - 1e-9
            and math.isfinite(float(pt[0]))
            and math.isfinite(float(pt[1]))
        ]
        if len(pts) < 3:
            continue

        valid_maps += 1
        rates = np.array([pt[0] for pt in pts], dtype=float)
        diffs = np.array([pt[1] for pt in pts], dtype=float)

        delta_r = np.maximum(rates[1:] - rates[:-1], 1e-6)
        delta_d = diffs[1:] - diffs[:-1]
        map_span_r = float(np.sum(delta_r))
        total_rate_span += map_span_r

        # Raw first-order gradients (preserved for diagnostic reporting)
        gradients = delta_d / delta_r
        abs_grad = np.abs(gradients)
        all_lipschitz.extend(abs_grad.tolist())

        # Shift-safe, scale-invariant normalization factor for this map
        d_min = float(np.min(diffs))
        d_max = float(np.max(diffs))
        floor_shift = min(0.0, d_min)
        mean_shifted = float(np.mean(diffs - floor_shift))
        scale_m = max(mean_shifted, d_max - d_min, 1.0)

        # Dimensionless normalized slope (relative difficulty change per 1.0x rate)
        norm_grads = gradients / scale_m
        all_norm_lipschitz.extend(np.abs(norm_grads).tolist())

        # Shift-safe step relative jump (for CliffEvent reporting)
        denom_step = np.maximum(diffs[:-1] - floor_shift, 1.0)
        rel_jumps = delta_d / denom_step

        # Step-size-normalized jump over a standard 0.02 fine rate step
        step_factor = np.minimum(delta_r, 0.02)
        eq_jumps = np.abs(norm_grads) * step_factor

        mids = 0.5 * (rates[:-1] + rates[1:])
        n_pts = len(norm_grads)
        impulses = np.zeros(n_pts, dtype=float)
        for i in range(n_pts):
            g_i = float(norm_grads[i])
            left_slice = norm_grads[max(0, i - 3) : i]
            right_slice = norm_grads[i + 1 : min(n_pts, i + 4)]

            if len(left_slice) > 0 and len(right_slice) > 0:
                g_left = float(np.median(left_slice))
                g_right = float(np.median(right_slice))
                hi = max(g_left, g_right)
                lo = min(g_left, g_right)
                if g_i > hi:
                    impulses[i] = g_i - hi
                elif g_i < lo:
                    impulses[i] = lo - g_i
            elif len(right_slice) >= 2:
                # Left boundary (i == 0): midpoint-aware extrapolation from i=1, 2
                denom_m = max(float(mids[2] - mids[1]), 1e-6)
                g_extrap = float(
                    norm_grads[1] - ((mids[1] - mids[0]) / denom_m) * (norm_grads[2] - norm_grads[1])
                )
                g_right = float(np.median(right_slice))
                hi = max(g_extrap, g_right)
                lo = min(g_extrap, g_right)
                if g_i > hi:
                    impulses[i] = g_i - hi
                elif g_i < lo:
                    impulses[i] = lo - g_i
            elif len(left_slice) >= 2:
                # Right boundary (i == n_pts - 1): midpoint-aware extrapolation from i=n-2, n-3
                denom_m = max(float(mids[-2] - mids[-3]), 1e-6)
                g_extrap = float(
                    norm_grads[-2] + ((mids[-1] - mids[-2]) / denom_m) * (norm_grads[-2] - norm_grads[-3])
                )
                g_left = float(np.median(left_slice))
                hi = max(g_extrap, g_left)
                lo = min(g_extrap, g_left)
                if g_i > hi:
                    impulses[i] = g_i - hi
                elif g_i < lo:
                    impulses[i] = lo - g_i

        map_roughnesses.append(float(np.sqrt(np.mean(impulses ** 2))))

        for i in range(n_pts):
            others = np.delete(norm_grads, i)
            med_other = float(np.median(others)) if len(others) > 0 else 0.0
            mad_other = float(np.median(np.abs(others - med_other))) if len(others) > 0 else 0.0
            robust_sigma = max(1.4826 * mad_other, 0.25 * abs(med_other), 0.20)

            imp = float(impulses[i])
            z_impulse = imp / robust_sigma
            eq_jump = float(eq_jumps[i])
            imp_jump = imp * float(step_factor[i])

            # A cliff is an isolated bilateral impulse that jumps past both neighboring
            # regimes and produces a significant fine-step difficulty discontinuity.
            is_cliff = (
                (z_impulse > z_threshold and eq_jump > rel_jump_threshold and imp_jump > 0.75 * rel_jump_threshold)
                or (imp > 2.5 and eq_jump > 1.5 * rel_jump_threshold)
            )

            if is_cliff:
                cliff_rate_span += float(delta_r[i])
                total_excess_jump += imp_jump
                cliff_events.append(
                    CliffEvent(
                        map_name=map_name,
                        rate_before=float(rates[i]),
                        rate_after=float(rates[i + 1]),
                        diff_before=float(diffs[i]),
                        diff_after=float(diffs[i + 1]),
                        local_gradient=float(gradients[i]),
                        relative_jump=float(rel_jumps[i]),
                    )
                )

    max_lip = float(np.max(all_lipschitz)) if all_lipschitz else 0.0
    mean_lip = float(np.mean(all_lipschitz)) if all_lipschitz else 0.0
    max_norm_lip = float(np.max(all_norm_lipschitz)) if all_norm_lipschitz else 0.0
    mean_roughness = float(np.mean(map_roughnesses)) if map_roughnesses else 0.0
    n_cliffs = len(cliff_events)
    cliff_rate = cliff_rate_span / total_rate_span if total_rate_span > 0.0 else 0.0
    mean_cliff_deficit = total_excess_jump / valid_maps if valid_maps > 0 else 0.0

    # Smoothness score in [0, 100]:
    # Strictly 100.0 when no routing/split cliffs occur (does not penalize
    # smooth nonlinear slope variations of empirical ND curves).
    if n_cliffs == 0:
        smoothness = 100.0
    else:
        smoothness = 100.0 * float(np.exp(-25.0 * cliff_rate - 15.0 * mean_cliff_deficit))
        smoothness = max(0.0, min(100.0, smoothness))

    # Sort cliff events by severity
    cliff_events.sort(key=lambda e: abs(e.relative_jump), reverse=True)

    return ContinuityResult(
        cliff_events_count=n_cliffs,
        max_lipschitz=max_lip,
        mean_lipschitz=mean_lip,
        smoothness_score=smoothness,
        cliff_events=cliff_events[:15],
        cliff_rate=cliff_rate,
        mean_roughness=mean_roughness,
        max_normalized_lipschitz=max_norm_lip,
        evaluated_maps=valid_maps,
    )
