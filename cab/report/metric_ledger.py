"""Metric input ledger: everything needed to re-derive the score without re-running.

Motivation
----------
The benchmark separates **measurement** (asking an algorithm for a difficulty)
from **conversion** (turning raw readings into a 0-100 score).  Before this
module, only the ACC payload was on disk (``predictions.csv``); the dynamic
dimensions (AIN / ASLN / CT / SP / AJ) kept their per-map inputs in memory, so
changing a conversion constant -- e.g. the RMSE decay, the Hill anchor, a β
weight -- forced a full ``--mode standard`` re-run of every algorithm.

The ledger closes that gap.  It is written next to the other artefacts and
contains:

``metric_inputs.csv``
    One row per beatmap: the raw prediction, the calibrated ND value, the
    jittered / short-LN-injected readings, and the per-map ``r*`` ("which clock
    rate would reproduce this reading") for both probes.
``speed_samples.csv``
    The per-probe-chart computation speed (ms/s) behind the SP median.
``metric_scalars.csv``
    ``key,value`` pairs: β weights, curve constants, probe aggregates
    (arrangement sensitivity, HO capability, ...), and the dimension scores the
    run actually produced (so a rescore can show a diff).

Combined with ``rate_curves.csv`` (the ``D(r)`` sweep), these four files are a
**sufficient statistic** for :func:`calculate_cab_score`: see
``selfcheck/tools/rescore_from_ledger.py``.

Nothing here recomputes a metric: the export is a projection of data the run
already produced, so writing it cannot change a score.
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cab.metrics.scoring import (
    ACC_RMSE_BASELINE,
    ACC_RMSE_DECAY,
    BETA,
    MACRO_INVERSION_PENALTY_CAP,
    MACRO_INVERSION_PENALTY_SCALE,
)
from cab.metrics.speed_metric import SP_HILL_GAMMA, SP_HILL_RHO0, SPEED_FLOOR_MS_PER_S
from cab.report._io import atomic_write_text
from cab.report.predictions_csv import _relativize

#: Bump when the column set or semantics change; the rescore tool refuses
#: ledgers it does not understand rather than silently mis-reading them.
LEDGER_SCHEMA_VERSION = 1

INPUT_COLUMNS: Tuple[str, ...] = (
    "path",
    "name",
    "pattern",
    "subPattern",
    "expected",
    "raw_prediction",
    "calibrated_prediction",
    "jitter_orig",
    "jitter_perturbed",
    "sln_orig",
    "sln_perturbed",
    "rstar_jitter",
    "rstar_sln",
)

SPEED_COLUMNS: Tuple[str, ...] = ("index", "speed_ms_per_s")

SCALAR_COLUMNS: Tuple[str, ...] = ("key", "value")
NOISE_COLUMNS: Tuple[str, ...] = ("name", "rate", "floor")


def _fmt(value: Any, digits: int = 6) -> str:
    """Format a cell, keeping NaN distinguishable from "not measured"."""
    if value is None:
        return ""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(number):
        return "NaN"
    if math.isinf(number):
        return "Inf" if number > 0 else "-Inf"
    return f"{number:.{digits}f}".rstrip("0").rstrip(".") or "0"


def _write_csv(rows: List[Dict[str, Any]], columns: Tuple[str, ...], path: Path) -> Optional[Path]:
    if not rows:
        return None
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    atomic_write_text(path, buffer.getvalue(), encoding="utf-8-sig")
    return path


def _invert(curve, target: float) -> Optional[float]:
    """Per-map ``r*`` from a rate curve (lazy import to avoid a cycle)."""
    from cab.runner.suite import _invert_rate_curve

    return _invert_rate_curve(curve, target)


def build_metric_input_rows(result: Any) -> List[Dict[str, Any]]:
    """Project one run into per-beatmap ledger rows."""
    samples = list(getattr(result, "samples", None) or [])
    if not samples:
        return []

    calibrated = list(getattr(result.accuracy, "calibrated_predictions", []) or [])
    raws = list(getattr(result, "raw_predictions", None) or [])
    jitter_ledger: Dict[str, Tuple[float, float]] = getattr(result, "jitter_ledger", None) or {}
    sln_ledger: Dict[str, Tuple[float, float]] = getattr(result, "sln_ledger", None) or {}
    curves: Dict[str, List[Tuple[float, float]]] = getattr(result, "rate_curves", None) or {}
    # r* resolved by on-demand probes (targets outside the sampled grid) never
    # lands in ``rate_curves``, so re-inverting would blank exactly those rows.
    # Prefer the values the run actually scored with; fall back to inversion for
    # results produced before 2026-10-22.
    rstar_jit_map: Dict[str, float] = getattr(result, "rstar_jitter", None) or {}
    rstar_sln_map: Dict[str, float] = getattr(result, "rstar_sln", None) or {}

    rows: List[Dict[str, Any]] = []
    for idx, sample in enumerate(samples):
        name = sample.name
        jit = jitter_ledger.get(name)
        sln = sln_ledger.get(name)
        curve = curves.get(name)

        rstar_jit = rstar_jit_map.get(name)
        if rstar_jit is None and jit and curve:
            rstar_jit = _invert(curve, float(jit[1]))
        rstar_sln = rstar_sln_map.get(name)
        if rstar_sln is None and sln and curve:
            rstar_sln = _invert(curve, float(sln[1]))

        rows.append(
            {
                "path": _relativize(sample.file_path),
                "name": name,
                "pattern": sample.pattern,
                "subPattern": getattr(sample, "sub_pattern", "") or "",
                "expected": _fmt(sample.expected, 6),
                "raw_prediction": _fmt(raws[idx]) if idx < len(raws) else "",
                "calibrated_prediction": _fmt(calibrated[idx]) if idx < len(calibrated) else "",
                "jitter_orig": _fmt(jit[0]) if jit else "",
                "jitter_perturbed": _fmt(jit[1]) if jit else "",
                "sln_orig": _fmt(sln[0]) if sln else "",
                "sln_perturbed": _fmt(sln[1]) if sln else "",
                "rstar_jitter": _fmt(rstar_jit) if rstar_jit is not None else "",
                "rstar_sln": _fmt(rstar_sln) if rstar_sln is not None else "",
            }
        )
    return rows


def build_scalar_rows(result: Any) -> List[Dict[str, Any]]:
    """Project one run into ``key,value`` scalars (constants + aggregates)."""
    score = getattr(result, "score", None)
    if score is None:
        # A result without a breakdown (legacy/hand-built) cannot be rescored;
        # writing constants-only scalars would look like a usable ledger.
        return []
    acc = result.accuracy
    inv = getattr(result, "inversion", None)
    cont = getattr(result, "continuity", None)
    jit = getattr(result, "jitter", None)
    sln = getattr(result, "short_ln", None)
    spd = getattr(result, "speed", None)

    pairs: List[Tuple[str, Any]] = [
        ("schema_version", LEDGER_SCHEMA_VERSION),
        ("algorithm", result.algorithm_name),
        ("scale_type", result.scale_type),
        ("tier_id", getattr(result, "tier_id", "all")),
        ("is_subtier", bool(getattr(score, "is_subtier", False))),
        ("is_out_of_domain", bool(getattr(score, "is_out_of_domain", False))),
        ("is_evaluated", bool(getattr(score, "is_evaluated", True))),
        # --- conversion constants (what a rescore would be testing) ---
        ("beta_accuracy", 0.0),
        ("beta_ain", BETA["Anti-Rate-Inversion"]),
        ("beta_asln", BETA["Anti-Short LN"]),
        ("beta_ct", BETA["Local Continuity"]),
        ("beta_sp", BETA["Computation Speed"]),
        ("beta_aj", BETA["Anti-Jitter"]),
        ("acc_rmse_decay", ACC_RMSE_DECAY),
        ("acc_rmse_baseline", ACC_RMSE_BASELINE),
        ("macro_penalty_scale", MACRO_INVERSION_PENALTY_SCALE),
        ("macro_penalty_cap", MACRO_INVERSION_PENALTY_CAP),
        ("speed_floor_ms_per_s", SPEED_FLOOR_MS_PER_S),
        ("sp_hill_rho0", SP_HILL_RHO0),
        ("sp_hill_gamma", SP_HILL_GAMMA),
        # --- accuracy aggregates ---
        ("acc_num_samples", getattr(acc, "num_samples", 0) or 0),
        ("acc_num_rejected_nonfinite", getattr(acc, "num_rejected_nonfinite", 0) or 0),
        ("acc_global_rmse", acc.global_rmse),
        ("acc_weighted_stratified_rmse", acc.weighted_stratified_rmse),
        ("acc_spearman_rho", acc.spearman_rho),
        ("acc_kendall_tau", acc.kendall_tau),
        # --- probe aggregates ---
        ("ain_inversion_frequency", getattr(inv, "inversion_frequency", None)),
        ("ain_auc_deficit", getattr(inv, "auc_deficit", None)),
        ("ain_total_maps", getattr(inv, "total_maps_evaluated", 0) if inv else 0),
        ("ct_smoothness_score", getattr(cont, "smoothness_score", None)),
        ("ct_evaluated_maps", getattr(cont, "evaluated_maps", 0) if cont else 0),
        ("aj_arrangement_sensitivity_pct", getattr(jit, "arrangement_sensitivity_pct", None)),
        ("aj_rstar_median", getattr(jit, "rstar_median", None)),
        ("aj_rstar_coverage", getattr(jit, "rstar_coverage", 0.0)),
        ("sln_ho_delta_pct", getattr(sln, "ho_delta_pct", None)),
        ("sln_is_brute_force_tap", bool(getattr(sln, "is_brute_force_tap", False))),
        ("sln_ln_excluded", bool(getattr(sln, "ln_excluded", False))),
        ("sln_rstar_median", getattr(sln, "rstar_median", None)),
        ("sln_rstar_coverage", getattr(sln, "rstar_coverage", 0.0)),
        # --- speed provenance (used when per-sample speeds are unavailable,
        #     e.g. an inherited --sp-source archive) ---
        ("sp_evaluated_count", getattr(spd, "evaluated_count", 0) if spd else 0),
        ("sp_median_ms_per_s", getattr(spd, "median_speed_ms_per_s", None) if spd else None),
        ("sp_baseline_ms_per_s", getattr(spd, "baseline_ms_per_s", None) if spd else None),
        ("sp_baseline_algorithm", getattr(spd, "baseline_algorithm", "") if spd else ""),
        ("sp_platform_overhead_ms", getattr(spd, "platform_overhead_ms", 0.0) if spd else 0.0),
        ("sp_rejected_samples", getattr(spd, "rejected_samples", 0) if spd else 0),
        # --- what this run actually produced (for the diff) ---
        ("computed_s_accuracy", getattr(score, "s_accuracy", None)),
        ("computed_s_ain", getattr(score, "s_inversion", None)),
        ("computed_s_asln", getattr(score, "s_short_ln", None)),
        ("computed_s_ct", getattr(score, "s_continuity", None)),
        ("computed_s_sp", getattr(score, "s_speed", None)),
        ("computed_s_aj", getattr(score, "s_jitter", None)),
        ("computed_macro_severity", getattr(score, "macro_severity", 0.0)),
        ("computed_macro_penalty", getattr(score, "macro_penalty", 0.0)),
        ("computed_total", getattr(score, "total_score", None)),
    ]
    return [{"key": k, "value": _fmt(v, 6) if isinstance(v, float) else ("" if v is None else str(v))}
            for k, v in pairs]


def build_speed_rows(result: Any) -> List[Dict[str, Any]]:
    spd = getattr(result, "speed", None)
    speeds = list(getattr(spd, "sample_speeds", []) or []) if spd else []
    return [{"index": i, "speed_ms_per_s": _fmt(value, 6)} for i, value in enumerate(speeds)]


def build_noise_floor_rows(result: Any) -> List[Dict[str, Any]]:
    """Flatten ``result.rate_noise_floors`` into ``name, rate, floor`` rows."""
    floors = getattr(result, "rate_noise_floors", None) or {}
    rows: List[Dict[str, Any]] = []
    for name in sorted(floors):
        per_rate = floors[name] or {}
        for rate in sorted(per_rate):
            rows.append(
                {"name": name, "rate": _fmt(rate, 4), "floor": _fmt(per_rate[rate], 6)}
            )
    return rows


def export_metric_ledger(result: Any, output_dir: Path) -> List[Path]:
    """Write ``metric_inputs.csv`` / ``speed_samples.csv`` / ``metric_scalars.csv``.

    Returns only the files actually written.  A run without per-map samples
    (legacy artefacts) writes nothing rather than an empty-looking ledger.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: List[Path] = []

    input_rows = build_metric_input_rows(result)
    if not input_rows:
        # No per-map payload => no ledger at all (an empty one would be
        # indistinguishable from a run that evaluated nothing).
        return []
    inputs = _write_csv(input_rows, INPUT_COLUMNS, out_dir / "metric_inputs.csv")
    if inputs:
        written.append(inputs)

    speeds = _write_csv(build_speed_rows(result), SPEED_COLUMNS, out_dir / "speed_samples.csv")
    if speeds:
        written.append(speeds)

    scalars = _write_csv(build_scalar_rows(result), SCALAR_COLUMNS, out_dir / "metric_scalars.csv")
    if scalars:
        written.append(scalars)

    floors = _write_csv(
        build_noise_floor_rows(result), NOISE_COLUMNS, out_dir / "noise_floors.csv"
    )
    if floors:
        written.append(floors)

    return written
