"""Dimension 4: Anti-Jitter / Timing Perturbation Robustness Evaluator.

Evaluates two coupled properties (Scheme 2):
1. Micro-Jitter Stability (inside the OD 300 hit window, <= ~10ms):
   Perturbations within 25% of W_300(OD) are imperceptible to player accuracy,
   so a robust algorithm should exhibit small relative drift (MAPE).
2. Arrangement Sensitivity Probe (anti-density-blindness gate):
   Algorithms that merely count macro window note density (e.g. osu!lazer /
   osu!mania 400ms linear-decay sums) appear artificially immune to micro-jitter
   only because they ignore intra-window note timing arrangement altogether.
   When smooth streams are bunched into minijack bursts at invariant 400ms
   window density, genuine pattern/speed algorithms respond strongly (~12-23%),
   whereas density-only algorithms barely react (~1.5%) and are penalized via a
   continuous Hill capability gate G_arrange(probe_delta_pct).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence
import numpy as np

#: Half-response threshold (in %) for the intra-window arrangement probe gate.
#: Algorithms with < ~2% response to same-density burst bunching are heavily
#: discounted, while genuine arrangement-aware algorithms (12-23%) receive ~1.0.
ARRANGEMENT_PROBE_TAU_PCT: float = 3.5

#: Steepness exponent of the continuous Hill capability gate.
ARRANGEMENT_PROBE_EXPONENT: float = 2.5


def arrangement_capability_gate(probe_delta_pct: Optional[float]) -> float:
    """Continuous [0, 1] gate penalizing arrangement-blind window-density counters."""
    if probe_delta_pct is None:
        return 1.0
    val = max(0.0, float(probe_delta_pct))
    if val <= 1e-9:
        return 0.0
    ratio = (val / ARRANGEMENT_PROBE_TAU_PCT) ** ARRANGEMENT_PROBE_EXPONENT
    return float(ratio / (1.0 + ratio))


def jitter_score(
    mape: float,
    arrangement_sensitivity_pct: Optional[float] = None,
) -> float:
    """Compute 0-100 Anti-Jitter score combining micro-jitter stability and arrangement gate."""
    m = max(0.0, float(mape))
    base = 100.0 / (1.0 + (m / 4.0) ** 1.6)
    gate = arrangement_capability_gate(arrangement_sensitivity_pct)
    return max(0.0, min(100.0, base * gate))


@dataclass
class JitterResult:
    jitter_rmse: float
    mape: float  # Mean Absolute Percentage Error (%)
    max_absolute_delta: float
    max_relative_delta: float
    variance_perturbation: float
    num_evaluated_maps: int
    arrangement_sensitivity_pct: Optional[float] = None
    is_arrangement_blind: bool = False

    @property
    def score(self) -> float:
        if self.num_evaluated_maps == 0:
            return 0.0
        return jitter_score(self.mape, self.arrangement_sensitivity_pct)


def evaluate_jitter(
    orig_difficulties: Sequence[float],
    jitter_difficulties: Sequence[float],
    arrangement_sensitivity_pct: Optional[float] = None,
) -> JitterResult:
    """Evaluate algorithmic sensitivity to OD-adaptive micro timing jitter."""
    orig = np.array(orig_difficulties, dtype=float)
    jitt = np.array(jitter_difficulties, dtype=float)

    if len(orig) == 0:
        return JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 0, arrangement_sensitivity_pct, False)

    deltas = jitt - orig
    abs_deltas = np.abs(deltas)

    # Shift-safe denominator prevents near-zero / negative ND baseline blowup
    d_min = float(min(0.0, np.min(orig)))
    denom = np.maximum(orig - d_min, 1.0)
    rel_deltas = abs_deltas / denom

    rmse = float(np.sqrt(np.mean(deltas ** 2)))
    mape = float(np.mean(rel_deltas)) * 100.0  # as percentage
    max_abs = float(np.max(abs_deltas))
    max_rel = float(np.max(rel_deltas))
    var = float(np.var(deltas))
    is_blind = (
        arrangement_sensitivity_pct is not None
        and float(arrangement_sensitivity_pct) < 3.0
    )

    return JitterResult(
        jitter_rmse=rmse,
        mape=mape,
        max_absolute_delta=max_abs,
        max_relative_delta=max_rel,
        variance_perturbation=var,
        num_evaluated_maps=len(orig),
        arrangement_sensitivity_pct=arrangement_sensitivity_pct,
        is_arrangement_blind=is_blind,
    )

