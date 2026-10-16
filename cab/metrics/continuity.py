"""Dimension 3: Local Continuity & Cliffing Robustness Evaluator.

Detects discrete jumps, hard threshold boundaries, and local Lipschitz bounds
under micro-step rate variations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Sequence, Tuple
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


def evaluate_continuity(
    rate_curves: Dict[str, Sequence[Tuple[float, float]]],
    z_threshold: float = 3.5,
    rel_jump_threshold: float = 0.04,
) -> ContinuityResult:
    """Evaluate continuity and detect cliffing events along rate curves.

    Args:
        rate_curves: Mapping from map_name to sequence of (clock_rate, difficulty).
        z_threshold: Z-score threshold for sudden gradient change.
        rel_jump_threshold: Minimum relative change to qualify as a cliff event.
    """
    all_lipschitz: List[float] = []
    cliff_events: List[CliffEvent] = []

    for map_name, curve in rate_curves.items():
        if len(curve) < 3:
            continue

        rates = np.array([pt[0] for pt in curve], dtype=float)
        diffs = np.array([pt[1] for pt in curve], dtype=float)

        delta_r = rates[1:] - rates[:-1]
        delta_d = diffs[1:] - diffs[:-1]

        # First-order gradients
        gradients = delta_d / np.maximum(delta_r, 1e-6)
        abs_grad = np.abs(gradients)
        all_lipschitz.extend(abs_grad.tolist())

        rel_jumps = delta_d / np.maximum(diffs[:-1], 1e-6)

        n_pts = len(gradients)
        window = 5
        half = window // 2

        for i in range(n_pts):
            # Compute rolling local median and std of gradient
            i_start = max(0, i - half)
            i_end = min(n_pts, i + half + 1)
            local_win = gradients[i_start:i_end]

            local_mean = float(np.mean(local_win))
            local_std = float(np.std(local_win)) if len(local_win) > 1 else 0.0

            z_score = abs(gradients[i] - local_mean) / (local_std + 1e-6)
            rel_jump = abs(rel_jumps[i])

            # Sudden gradient spike or sharp jump exceeding threshold
            is_cliff = (
                (z_score > z_threshold and rel_jump > rel_jump_threshold)
                or (abs(gradients[i]) > 50.0 and rel_jump > 0.08)
            )

            if is_cliff:
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
    n_cliffs = len(cliff_events)

    # Smoothness score in [0, 100]
    # Penalizes cliff events and extreme Lipschitz divergence
    penalty_cliff = min(60.0, n_cliffs * 1.5)
    penalty_lip = min(40.0, max(0.0, (mean_lip - 15.0) * 1.5))
    smoothness = max(0.0, 100.0 - penalty_cliff - penalty_lip)

    # Sort cliff events by severity
    cliff_events.sort(key=lambda e: abs(e.relative_jump), reverse=True)

    return ContinuityResult(
        cliff_events_count=n_cliffs,
        max_lipschitz=max_lip,
        mean_lipschitz=mean_lip,
        smoothness_score=smoothness,
        cliff_events=cliff_events[:15],
    )
