"""Dimension 4: Anti-Jitter / Timing Perturbation Robustness Evaluator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence
import numpy as np


@dataclass
class JitterResult:
    jitter_rmse: float
    mape: float  # Mean Absolute Percentage Error (%)
    max_absolute_delta: float
    max_relative_delta: float
    variance_perturbation: float
    num_evaluated_maps: int


def evaluate_jitter(
    orig_difficulties: Sequence[float],
    jitter_difficulties: Sequence[float],
) -> JitterResult:
    """Evaluate algorithmic sensitivity to microscopic timing jitter (sigma=8ms)."""
    orig = np.array(orig_difficulties, dtype=float)
    jitt = np.array(jitter_difficulties, dtype=float)

    if len(orig) == 0:
        return JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 0)

    deltas = jitt - orig
    abs_deltas = np.abs(deltas)
    rel_deltas = np.where(orig > 1e-6, abs_deltas / orig, 0.0)

    rmse = float(np.sqrt(np.mean(deltas ** 2)))
    mape = float(np.mean(rel_deltas)) * 100.0  # as percentage
    max_abs = float(np.max(abs_deltas))
    max_rel = float(np.max(rel_deltas))
    var = float(np.var(deltas))

    return JitterResult(
        jitter_rmse=rmse,
        mape=mape,
        max_absolute_delta=max_abs,
        max_relative_delta=max_rel,
        variance_perturbation=var,
        num_evaluated_maps=len(orig),
    )
