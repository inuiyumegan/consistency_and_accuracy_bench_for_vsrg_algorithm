"""Machine-readable JSON benchmark export."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from cab.report.environment import collect_environment_info
from cab.report.profiling import profile_domains
from cab.runner.suite import BenchmarkRunResult, MultiTierBenchmarkResult


def _build_declared_scope(result) -> Optional[Dict[str, Any]]:
    """Serialize the declared difficulty scope (secondary in-scope reading).

    ``None`` when the algorithm declared no scope, so consumers see an explicit
    ``null`` and never mistake absence for an empty scope. The headline metrics
    are untouched by design -- this block only restates what was declared plus
    the supplementary in-scope Accuracy reading.
    """
    scope = getattr(result, "scope_report", None)
    if scope is None or not scope.declarations:
        return None
    in_acc = getattr(result, "in_scope_accuracy", None)
    block: Dict[str, Any] = {
        "description": scope.description,
        "declarations": [
            {
                "patterns": list(d.patterns),
                "min_nd": d.min_nd,
                "max_nd": d.max_nd,
            }
            for d in scope.declarations
        ],
        "in_scope_count": len(scope.in_scope_samples),
        "out_of_scope_count": len(scope.out_of_scope_samples),
        "usage_given_up": round(scope.out_of_scope_usage, 4),
        "in_scope_accuracy": None,
    }
    if in_acc is not None:
        from cab.metrics.scoring import score_accuracy

        # Scored by the SAME standard as the headline ACC dimension, but with
        # the structural-inversion penalty recomputed from the in-scope cohort
        # anchors.  Reusing the full-run penalty would double-charge: dropping
        # the charts responsible for a dip leaves a largely monotonic anchor
        # sequence (Dan-Overlay: S = 0.292 full-run vs 0.021 in scope), so the
        # same declaration would be billed twice for a defect it no longer has.
        in_score, in_severity, in_penalty = score_accuracy(in_acc)
        block["in_scope_accuracy"] = {
            "calibrated_rmse": round(in_acc.global_rmse, 4),
            "global_mae": round(in_acc.global_mae, 4),
            "spearman_rho": round(in_acc.spearman_rho, 4),
            "mean_wtd": round(in_acc.mean_wtd, 4),
            "weighted_stratified_rmse": round(in_acc.weighted_stratified_rmse, 4),
            "sample_count": len(in_acc.calibrated_predictions),
            # 0-100 sub-score, comparable with the "Ground-Truth Accuracy"
            # dimension of the headline run (same formula, own anchors).
            "score": round(in_score, 2),
            "macro_severity": round(in_severity, 4),
            "macro_penalty": round(in_penalty, 2),
        }
    return block


def _build_domain_profile(acc) -> Dict[str, Any]:
    """Serialize the programmatic strengths/weaknesses profile of an accuracy result."""
    prof = profile_domains(
        getattr(acc, "stratified_rmse", {}) or {},
        pattern_counts=getattr(acc, "stratified_counts", None),
    )
    return {
        "baseline_rmse": round(prof.baseline_rmse, 4),
        "min_samples": prof.min_samples,
        "has_judged": prof.has_judged,
        "strengths": [v.pattern for v in prof.strengths],
        "weaknesses": [v.pattern for v in prof.weaknesses],
        "adequate": [v.pattern for v in prof.adequate],
        "insufficient": [v.pattern for v in prof.insufficient],
        "verdicts": [
            {
                "pattern": v.pattern,
                "rmse": round(v.rmse, 4),
                "samples": v.samples,
                "ratio": round(v.ratio, 4),
                "verdict": v.verdict,
            }
            for v in prof.verdicts
        ],
    }


def export_to_json(result: BenchmarkRunResult, output_path: str | Path) -> Path:
    """Export benchmark run results to JSON file."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    data: Dict[str, Any] = {
        "benchmark": "CAB-VA",
        "algorithm": result.algorithm_name,
        "scale_type": result.scale_type,
        "tier_id": getattr(result, "tier_id", "all"),
        "tier_name": getattr(result, "tier_name", "全域段 (All Range)"),
        "mode": result.config.mode,
        "duration_seconds": round(result.duration_seconds, 2),
        "environment": collect_environment_info(),
        "dataset_filters": {
            "has_filters": result.config.has_filters(),
            "summary": result.filter_summary,
            "patterns": list(result.config.patterns) if result.config.patterns else None,
            "exclude_patterns": list(result.config.exclude_patterns) if result.config.exclude_patterns else None,
            "min_expected": result.config.min_expected,
            "max_expected": result.config.max_expected,
            "name_filter": result.config.name_filter,
            "filter_preset": result.config.filter_preset,
            "sample_stats": result.sample_stats,
        },
        "score_summary": {
            "total_score": result.score.total_score,
            "max_score": 100.0,
            "is_subtier": result.score.is_subtier,
            "is_out_of_domain": result.score.is_out_of_domain,
            "betas": getattr(result.score, "dimension_betas", {}),
            "discounts": getattr(result.score, "dimension_discounts", {}),
            "dimensions": {k: (round(v, 2) if v is not None else None) for k, v in result.score.dimension_scores.items()},
            # Structural-inversion ("倒挂") penalty — already folded into ACC:
            "macro_penalty": getattr(result.score, "macro_penalty", 0.0),
            "macro_severity": getattr(result.score, "macro_severity", 0.0),
        },
        "failed_beatmaps": getattr(result, "failed_beatmaps", []),
        "declared_scope": _build_declared_scope(result),
        "dimension_1_accuracy": {
            "global_rmse": round(result.accuracy.global_rmse, 4),
            "global_mae": round(result.accuracy.global_mae, 4),
            "spearman_rho": round(result.accuracy.spearman_rho, 4),
            "spearman_p": result.accuracy.spearman_p,
            "kendall_tau": round(result.accuracy.kendall_tau, 4),
            "stratified_rmse": {
                k: round(v, 4) for k, v in result.accuracy.stratified_rmse.items()
            },
            "stratified_counts": {
                k: int(v) for k, v in getattr(result.accuracy, "stratified_counts", {}).items()
            },
            "domain_profile": _build_domain_profile(result.accuracy),
            "weighted_stratified_rmse": round(result.accuracy.weighted_stratified_rmse, 4),
            "mean_within_tier_dispersion": round(result.accuracy.mean_wtd, 4),
            # Cohort anchors (tier ND -> centroid) in the raw scale, recorded so
            # offline tooling (e.g. selfcheck/tools/recompute_scores.py) can
            # re-derive the structural-inversion ("倒挂") severity without
            # re-running the algorithms.
            "raw_anchors": {
                str(float(t)): round(float(v), 6)
                for t, v in getattr(result.accuracy.calibration, "raw_anchors", {}).items()
            },
            "macro_inversion_alerts": [
                {
                    "lower_tier": a.lower_tier,
                    "higher_tier": a.higher_tier,
                    "lower_anchor": round(a.lower_anchor, 4),
                    "higher_anchor": round(a.higher_anchor, 4),
                    "violation": round(a.violation_amount, 4),
                }
                for a in result.accuracy.calibration.macro_alerts
            ],
        },
        "dimension_2_inversion": {
            "inversion_frequency": round(result.inversion.inversion_frequency, 5),
            "auc_deficit": round(result.inversion.auc_deficit, 6),
            "max_relative_drop": round(result.inversion.max_relative_drop, 4),
            "max_absolute_drop": round(result.inversion.max_absolute_drop, 4),
            "inverted_maps_count": result.inversion.inverted_maps_count,
            "total_maps_evaluated": result.inversion.total_maps_evaluated,
            "composite_inversion_index": round(result.inversion.composite_inversion_index, 4),
            "worst_maps": [
                {
                    "map_name": s.map_name,
                    "steps_inverted": s.steps_inverted,
                    "inversion_ratio": round(s.inversion_ratio, 4),
                    "auc_deficit": round(s.auc_deficit, 6),
                    "max_relative_drop": round(s.max_relative_drop, 4),
                    "worst_transition": {
                        "rate_from": round(s.worst_transition[0], 3),
                        "rate_to": round(s.worst_transition[1], 3),
                        "diff_from": round(s.worst_transition[2], 4),
                        "diff_to": round(s.worst_transition[3], 4),
                    },
                }
                for s in result.inversion.worst_maps[:10]
            ],
        } if result.inversion is not None else None,
        "dimension_3_continuity": {
            "cliff_events_count": result.continuity.cliff_events_count,
            "max_lipschitz": round(result.continuity.max_lipschitz, 2),
            "mean_lipschitz": round(result.continuity.mean_lipschitz, 2),
            "smoothness_score": round(result.continuity.smoothness_score, 2),
            "cliff_events": [
                {
                    "map_name": e.map_name,
                    "rate_before": round(e.rate_before, 3),
                    "rate_after": round(e.rate_after, 3),
                    "diff_before": round(e.diff_before, 4),
                    "diff_after": round(e.diff_after, 4),
                    "relative_jump": round(e.relative_jump, 4),
                    "local_gradient": round(e.local_gradient, 2),
                }
                for e in result.continuity.cliff_events[:10]
            ],
        } if result.continuity is not None else None,
        "dimension_4_jitter": {
            "jitter_rmse": round(result.jitter.jitter_rmse, 6),
            "mape": round(result.jitter.mape, 4),
            "max_absolute_delta": round(result.jitter.max_absolute_delta, 6),
            "max_relative_delta": round(result.jitter.max_relative_delta, 6),
            "variance_perturbation": round(result.jitter.variance_perturbation, 8),
            "num_evaluated_maps": result.jitter.num_evaluated_maps,
        },
        "dimension_5_short_ln": {
            "short_ln_rmse": round(result.short_ln.short_ln_rmse, 6),
            "inflation_tendency_ratio": round(result.short_ln.inflation_tendency_ratio, 4),
            "max_absolute_delta": round(result.short_ln.max_absolute_delta, 6),
            "has_native_immunity": result.short_ln.has_native_immunity,
            "immunity_grade": result.short_ln.immunity_grade,
            "num_evaluated_maps": result.short_ln.num_evaluated_maps,
        },
        "dimension_6_speed": {
            "average_speed_ms_per_s": round(result.speed.average_speed_ms_per_s, 4) if hasattr(result, "speed") and result.speed else 0.0,
            "median_speed_ms_per_s": round(result.speed.median_speed_ms_per_s, 4) if hasattr(result, "speed") and result.speed else 0.0,
            "p95_speed_ms_per_s": round(result.speed.p95_speed_ms_per_s, 4) if hasattr(result, "speed") and result.speed else 0.0,
            "max_speed_ms_per_s": round(result.speed.max_speed_ms_per_s, 4) if hasattr(result, "speed") and result.speed else 0.0,
            "realtime_factor": round(result.speed.realtime_factor, 1) if hasattr(result, "speed") and result.speed else 0.0,
            "speed_score": round(result.speed.speed_score, 2) if hasattr(result, "speed") and result.speed else 0.0,
            "evaluated_count": result.speed.evaluated_count if hasattr(result, "speed") and result.speed else 0,
        },
    }

    with open(out_file, "w", encoding="utf-8") as fp:
        json.dump(data, fp, indent=2, ensure_ascii=False)

    return out_file


def export_multi_tier_to_json(result: MultiTierBenchmarkResult, output_path: str | Path) -> Path:
    """Export summary of all 4 difficulty tiers to JSON file."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    summary_data: Dict[str, Any] = {
        "benchmark": "CAB-VA",
        "algorithm": result.algorithm_name,
        "scale_type": result.scale_type,
        "mode": result.config.mode,
        "duration_seconds": round(result.duration_seconds, 2),
        "environment": collect_environment_info(),
        "failed_beatmaps": getattr(result, "failed_beatmaps", []),
        "declared_scope": _build_declared_scope(result.master_result),
        "tiers": {},
    }

    for tier_id, tr in result.tier_results.items():
        summary_data["tiers"][tier_id] = {
            "tier_name": tr.tier_name,
            "sample_count": len(tr.accuracy.calibrated_predictions),
            "total_score": round(tr.score.total_score, 2),
            "is_subtier": tr.score.is_subtier,
            "is_out_of_domain": tr.score.is_out_of_domain,
            "dimension_scores": {k: (round(v, 2) if v is not None else None) for k, v in tr.score.dimension_scores.items()},
            "accuracy": {
                "calibrated_rmse": round(tr.accuracy.global_rmse, 4),
                "global_mae": round(tr.accuracy.global_mae, 4),
                "spearman_rho": round(tr.accuracy.spearman_rho, 4),
                "mean_wtd": round(tr.accuracy.mean_wtd, 4),
                "stratified_rmse": {
                    k: round(v, 4) for k, v in tr.accuracy.stratified_rmse.items()
                },
                "domain_profile": _build_domain_profile(tr.accuracy),
            },
            "inversion": {
                "inversion_frequency": round(tr.inversion.inversion_frequency, 5),
                "auc_deficit": round(tr.inversion.auc_deficit, 6),
                "max_relative_drop": round(tr.inversion.max_relative_drop, 4),
            } if tr.inversion is not None else None,
            "continuity": {
                "cliff_events_count": tr.continuity.cliff_events_count,
                "smoothness_score": round(tr.continuity.smoothness_score, 2),
            } if tr.continuity is not None else None,
            "short_ln": {
                "short_ln_rmse": round(tr.short_ln.short_ln_rmse, 6),
                "inflation_tendency_ratio": round(tr.short_ln.inflation_tendency_ratio, 4),
                "immunity_grade": tr.short_ln.immunity_grade,
            },
            "speed": {
                "average_speed_ms_per_s": round(tr.speed.average_speed_ms_per_s, 4),
                "p95_speed_ms_per_s": round(tr.speed.p95_speed_ms_per_s, 4),
                "max_speed_ms_per_s": round(tr.speed.max_speed_ms_per_s, 4),
                "realtime_factor": round(tr.speed.realtime_factor, 1),
                "speed_score": round(tr.speed.speed_score, 2),
            },
            "jitter": {
                "jitter_rmse": round(tr.jitter.jitter_rmse, 6),
                "mape": round(tr.jitter.mape, 4),
            },
        }

    with open(out_file, "w", encoding="utf-8") as fp:
        json.dump(summary_data, fp, indent=2, ensure_ascii=False)

    return out_file

