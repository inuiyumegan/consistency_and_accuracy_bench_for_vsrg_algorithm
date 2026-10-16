"""CAB-VA Benchmark Suite Orchestrator.

Coordinates execution across all five core benchmark dimensions:
1. Ground-Truth Accuracy & Calibrated ng-RMSE (includes course)
2. Anti-Rate-Inversion (strictly excludes course)
3. Local Continuity & Cliffing (strictly excludes course)
4. Anti-Jitter Robustness (strictly excludes course)
5. Anti-Short LN Robustness (strictly excludes course)
"""

from __future__ import annotations

import multiprocessing as mp
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from cab.adapter.base import BaseAdapter
from cab.adapter.cli import CliAdapter
from cab.adapter.daniel import DanielAdapter
from cab.metrics import (
    AccuracyResult,
    ContinuityResult,
    InversionResult,
    JitterResult,
    ScoreBreakdown,
    ShortLNResult,
    SpeedResult,
    calculate_cab_score,
    evaluate_accuracy,
    evaluate_continuity,
    evaluate_jitter,
    evaluate_rate_inversion,
    evaluate_short_ln,
    evaluate_speed,
)
from cab.mutator.hold_off import apply_hold_off
from cab.parser.osu_parser import Beatmap
from cab.parser.bench_loader import BenchmarkDataset, BenchmarkSample
from cab.runner.config import (
    BenchConfig,
    TierDefinition,
    TIER_DEFINITIONS,
    DEFAULT_TIERS,
    TIER_ALL,
    TIER_NUMBER,
    TIER_GREEK,
    TIER_INHUMAN,
    preset_excludes_pattern,
)
from cab.runner.worker import (
    init_worker,
    run_calc_content,
    run_calc_ho_probe_job,
    run_calc_jitter_job,
    run_calc_path,
    run_calc_path_timed,
    run_calc_rate_job,
    run_calc_short_ln_job,
)


@dataclass
class BenchmarkRunResult:
    algorithm_name: str
    scale_type: str
    config: BenchConfig
    accuracy: AccuracyResult
    inversion: Optional[InversionResult]
    continuity: Optional[ContinuityResult]
    jitter: JitterResult
    short_ln: ShortLNResult
    speed: SpeedResult
    score: ScoreBreakdown
    rate_curves: Dict[str, List[Tuple[float, float]]]
    duration_seconds: float
    filter_summary: str = "None (Full Dataset)"
    sample_stats: Dict[str, Any] = field(default_factory=dict)
    tier_id: str = "all"
    tier_name: str = "全域段 (All Range)"
    failed_beatmaps: List[Dict[str, str]] = field(default_factory=list)


@dataclass
class MultiTierBenchmarkResult:
    """Benchmark result containing multi-tier evaluation breakdown."""

    algorithm_name: str
    scale_type: str
    config: BenchConfig
    duration_seconds: float
    tier_results: Dict[str, BenchmarkRunResult]
    master_result: BenchmarkRunResult

    @property
    def score(self) -> ScoreBreakdown:
        return self.master_result.score

    @property
    def failed_beatmaps(self) -> List[Dict[str, str]]:
        return self.master_result.failed_beatmaps

    @property
    def accuracy(self) -> AccuracyResult:
        return self.master_result.accuracy

    @property
    def inversion(self) -> InversionResult:
        return self.master_result.inversion

    @property
    def continuity(self) -> ContinuityResult:
        return self.master_result.continuity

    @property
    def jitter(self) -> JitterResult:
        return self.master_result.jitter

    @property
    def short_ln(self) -> ShortLNResult:
        return self.master_result.short_ln

    @property
    def speed(self) -> SpeedResult:
        return self.master_result.speed

    @property
    def rate_curves(self) -> Dict[str, List[Tuple[float, float]]]:
        return self.master_result.rate_curves

    @property
    def filter_summary(self) -> str:
        return self.master_result.filter_summary

    @property
    def sample_stats(self) -> Dict[str, Any]:
        return self.master_result.sample_stats

    @property
    def tier_id(self) -> str:
        return self.master_result.tier_id

    @property
    def tier_name(self) -> str:
        return self.master_result.tier_name


def _matches_tier(sample: BenchmarkSample, tier: TierDefinition) -> bool:
    val = float(sample.expected)
    if tier.min_val != -float("inf"):
        min_ok = (val >= tier.min_val) if tier.min_inclusive else (val > tier.min_val)
    else:
        min_ok = True
    if tier.max_val != float("inf"):
        max_ok = (val <= tier.max_val) if tier.max_inclusive else (val < tier.max_val)
    else:
        max_ok = True
    return min_ok and max_ok


def _load_speed_result(source_dir: str) -> Optional[SpeedResult]:
    """Load a SpeedResult from another run's exported JSON.

    ``source_dir`` may be either a results directory containing
    ``cab_benchmark_result.json`` (or ``all/cab_benchmark_result.json``) or a
    direct path to such a JSON file. Returns None when nothing usable is found,
    so callers can fall back to the freshly measured speed.
    """
    import json

    base = Path(source_dir)
    candidates = []
    if base.is_file():
        candidates.append(base)
    else:
        candidates.append(base / "cab_benchmark_result.json")
        candidates.append(base / "all" / "cab_benchmark_result.json")

    for cand in candidates:
        try:
            if not cand.is_file():
                continue
            data = json.loads(cand.read_text(encoding="utf-8"))
            sp = data.get("dimension_6_speed")
            if not isinstance(sp, dict):
                continue
            return SpeedResult(
                average_speed_ms_per_s=float(sp.get("average_speed_ms_per_s", 0.0)),
                median_speed_ms_per_s=float(sp.get("median_speed_ms_per_s", 0.0)),
                realtime_factor=float(sp.get("realtime_factor", float("inf"))),
                speed_score=float(sp.get("speed_score", 0.0)),
                evaluated_count=int(sp.get("evaluated_count", 0)),
                sample_speeds=[],
                p95_speed_ms_per_s=float(sp.get("p95_speed_ms_per_s", 0.0)),
                max_speed_ms_per_s=float(sp.get("max_speed_ms_per_s", 0.0)),
            )
        except Exception:  # noqa: BLE001
            continue
    return None


def _find_suspicious_intervals(curve) -> list:
    """Detect rate intervals [r_k, r_{k+1}] that warrant a finer sweep.

    A pair is suspicious when (1) the difficulty drops between adjacent rates
    (potential inversion) or (2) the local gradient deviates strongly from the
    map's median gradient (potential cliff). Used by the adaptive two-phase
    rate sweep to concentrate fine sampling where problems actually are.
    """
    if not curve or len(curve) < 3:
        return []
    rs = [pt[0] for pt in curve]
    ds = [pt[1] for pt in curve]
    suspicious = set()

    # 1) Non-monotonic adjacent pairs
    for k in range(len(ds) - 1):
        if ds[k + 1] < ds[k] - 1e-9:
            suspicious.add((rs[k], rs[k + 1]))

    # 2) Gradient outliers vs. the map's median gradient
    grads = [
        (ds[k + 1] - ds[k]) / max(rs[k + 1] - rs[k], 1e-9)
        for k in range(len(ds) - 1)
    ]
    abs_grads = [abs(g) for g in grads]
    med = float(np.median(abs_grads)) if abs_grads else 0.0
    for k, g in enumerate(grads):
        if abs(g) > max(5.0 * med, 1e-6):
            suspicious.add((rs[k], rs[k + 1]))
            if k > 0:
                suspicious.add((rs[k - 1], rs[k]))
            if k + 1 < len(rs) - 1:
                suspicious.add((rs[k + 1], rs[k + 2]))
    return sorted(suspicious)


class BenchmarkSuite:
    """Master benchmark coordinator."""

    def __init__(
        self,
        adapter: BaseAdapter,
        config: Optional[BenchConfig] = None,
        dataset: Optional[BenchmarkDataset] = None,
    ):
        self.adapter = adapter
        self.config = config or BenchConfig.create("standard")
        if dataset is None:
            raw_dataset = BenchmarkDataset.load_from_csv(
                csv_path=self.config.csv_path,
                samples_root=self.config.samples_root,
            )
        else:
            raw_dataset = dataset

        # Apply dataset filters if configured
        if self.config.has_filters():
            self.dataset = raw_dataset.filter(
                patterns=self.config.patterns,
                exclude_patterns=self.config.exclude_patterns,
                min_expected=self.config.min_expected,
                max_expected=self.config.max_expected,
                name_filter=self.config.name_filter,
            )
            if len(self.dataset) == 0:
                raise ValueError(
                    f"Dataset is empty after applying filters: {self.config.get_filter_summary()}"
                )
        else:
            self.dataset = raw_dataset

    def _get_worker_spec(self) -> Tuple[str, Dict[str, Any]]:
        """Extract serializable parameters for worker initialization."""
        if isinstance(self.adapter, DanielAdapter):
            return (
                "daniel",
                {
                    "daniel_root": getattr(self.adapter, "src_dir", None),
                    "scale_type": self.adapter.scale_type,
                },
            )
        elif isinstance(self.adapter, CliAdapter):
            return (
                "cli",
                {
                    "name": self.adapter.name,
                    "cmd_template": self.adapter.cmd_template,
                    "scale_type": self.adapter.scale_type,
                    "timeout_seconds": self.adapter.timeout_seconds,
                    "server_cmd": getattr(self.adapter, "server_cmd", None),
                },
            )
        else:
            raise TypeError(f"Unsupported adapter type for multiprocessing: {type(self.adapter)}")

    def _select_stratified_samples(
        self,
        samples: Sequence[BenchmarkSample],
        limit: Optional[int],
    ) -> List[BenchmarkSample]:
        """Stratified sample selection across patterns and difficulty tiers."""
        if limit is None or limit >= len(samples):
            return list(samples)

        rng = np.random.RandomState(self.config.seed)
        # Group by pattern
        by_pattern: Dict[str, List[BenchmarkSample]] = {}
        for s in samples:
            by_pattern.setdefault(s.pattern.lower(), []).append(s)

        selected: List[BenchmarkSample] = []
        n_patterns = len(by_pattern)
        per_pattern = max(1, limit // n_patterns)

        for pat, p_samples in by_pattern.items():
            # Sort by expected difficulty to ensure tier diversity
            p_samples_sorted = sorted(p_samples, key=lambda s: s.expected)
            n_p = len(p_samples_sorted)
            if n_p <= per_pattern:
                selected.extend(p_samples_sorted)
            else:
                # Uniform quantile sampling
                indices = np.linspace(0, n_p - 1, per_pattern, dtype=int)
                selected.extend([p_samples_sorted[i] for i in indices])

        # Fill remaining slots if any
        if len(selected) < limit:
            remaining = [s for s in samples if s not in selected]
            if remaining:
                chosen_idx = rng.choice(
                    len(remaining), size=min(limit - len(selected), len(remaining)), replace=False
                )
                selected.extend([remaining[i] for i in chosen_idx])

        return selected[:limit]

    def _evaluate_tier_slice(
        self,
        tier: TierDefinition,
        acc_samples: Sequence[BenchmarkSample],
        raw_predictions: Sequence[float],
        acc_timings: Sequence[Tuple[float, float]],
        pert_samples: Sequence[BenchmarkSample],
        rate_curves: Dict[str, List[Tuple[float, float]]],
        jitter_map: Dict[str, Tuple[float, float]],
        sln_map: Dict[str, Tuple[float, float]],
        is_brute_force_tap: bool,
        ln_excluded: bool,
        elapsed: float,
    ) -> BenchmarkRunResult:
        """Slice evaluated pool predictions and compute metrics for a single tier."""
        is_subtier = (tier.tier_id != TIER_ALL)

        # 1. Sliced accuracy & timings
        tier_acc_idx = [i for i, s in enumerate(acc_samples) if _matches_tier(s, tier)]
        tier_acc_samples = [acc_samples[i] for i in tier_acc_idx]
        tier_raw_preds = [raw_predictions[i] for i in tier_acc_idx]
        tier_acc_timings = [acc_timings[i] for i in tier_acc_idx]

        is_out_of_domain = (len(tier_acc_samples) == 0)

        tier_acc_res = evaluate_accuracy(
            samples=tier_acc_samples,
            raw_predictions=tier_raw_preds,
            scale_type=self.adapter.scale_type,
        )
        tier_speed_res = evaluate_speed(tier_acc_timings)

        # SP provenance override (see run_all): drop-in replacements inherit the
        # original algorithm's Speed dimension so the score reflects the
        # original's real speed rather than the replacement's.
        if self.config.sp_source:
            overridden_sp = _load_speed_result(self.config.sp_source)
            if overridden_sp is not None:
                tier_speed_res = overridden_sp

        # 2. Perturbation & dynamic rate scaling
        if is_subtier:
            # Sub-tiers (Number, Greek, Inhuman) do NOT evaluate dynamic rate scaling (Dim 2 AIN & Dim 4 CT)
            tier_rate_curves: Dict[str, List[Tuple[float, float]]] = {}
            tier_inv_res: Optional[InversionResult] = None
            tier_cont_res: Optional[ContinuityResult] = None
        else:
            tier_pert_samples = [s for s in pert_samples if _matches_tier(s, tier)]
            tier_rate_curves = {
                s.name: rate_curves[s.name]
                for s in tier_pert_samples
                if s.name in rate_curves
            }
            tier_inv_res = evaluate_rate_inversion(tier_rate_curves)
            tier_cont_res = evaluate_continuity(tier_rate_curves)

        # 3. Microscopic perturbation (Jitter & Short-LN)
        tier_pert_samples = [s for s in pert_samples if _matches_tier(s, tier)]
        tier_jitter_orig = [jitter_map[s.name][0] for s in tier_pert_samples if s.name in jitter_map]
        tier_jitter_diff = [jitter_map[s.name][1] for s in tier_pert_samples if s.name in jitter_map]
        tier_jitter_res = evaluate_jitter(tier_jitter_orig, tier_jitter_diff)

        tier_sln_orig = [sln_map[s.name][0] for s in tier_pert_samples if s.name in sln_map]
        tier_sln_diff = [sln_map[s.name][1] for s in tier_pert_samples if s.name in sln_map]
        tier_sln_res = evaluate_short_ln(
            tier_sln_orig,
            tier_sln_diff,
            is_brute_force_tap=is_brute_force_tap,
            ln_excluded=ln_excluded,
        )

        tier_score = calculate_cab_score(
            accuracy=tier_acc_res,
            inversion=tier_inv_res,
            continuity=tier_cont_res,
            jitter=tier_jitter_res,
            short_ln=tier_sln_res,
            speed=tier_speed_res,
            is_subtier=is_subtier,
            is_out_of_domain=is_out_of_domain,
        )

        # Dataset stats for this tier
        try:
            tier_dataset = self.dataset.filter(
                min_expected=tier.min_val if tier.min_val != -float("inf") else None,
                max_expected=tier.max_val if tier.max_val != float("inf") else None,
                min_inclusive=tier.min_inclusive,
                max_inclusive=tier.max_inclusive,
            )
            tier_stats = tier_dataset.get_stats_summary()
        except Exception:
            tier_stats = {"total": len(tier_acc_samples), "patterns": {}}

        return BenchmarkRunResult(
            algorithm_name=self.adapter.name,
            scale_type=self.adapter.scale_type,
            config=self.config,
            accuracy=tier_acc_res,
            inversion=tier_inv_res,
            continuity=tier_cont_res,
            jitter=tier_jitter_res,
            short_ln=tier_sln_res,
            speed=tier_speed_res,
            score=tier_score,
            rate_curves=tier_rate_curves,
            duration_seconds=elapsed,
            filter_summary=f"Tier: {tier.name} ({tier.description})",
            sample_stats=tier_stats,
            tier_id=tier.tier_id,
            tier_name=tier.name,
        )

    def run_all(self, verbose: bool = True) -> BenchmarkRunResult | MultiTierBenchmarkResult:
        """Run all 6 benchmark dimensions across single or multi-tier configurations."""
        t_start = time.time()
        adapter_type, adapter_kwargs = self._get_worker_spec()
        ctx = mp.get_context("spawn")

        if verbose:
            print(f"============================================================")
            print(f"CAB-VA: Consistency & Accuracy Bench for VSRG Algorithm")
            print(f"Target Algorithm: {self.adapter.name} (Scale: {self.adapter.scale_type})")
            print(f"Execution Mode:   {self.config.mode.upper()} (Workers: {self.config.workers})")
            print(f"Tier Evaluation:  {self.config.tier_mode.upper()} (Default 4-Tiers Slicing)")
            if self.config.has_filters():
                stats = self.dataset.get_stats_summary()
                pat_str = ", ".join(f"{k}: {v}" for k, v in sorted(stats['patterns'].items()))
                print(f"Dataset Filters:  {self.config.get_filter_summary()}")
                print(f"Filtered Samples: {stats['total']} maps ({pat_str})")
            print(f"============================================================")

        with ctx.Pool(
            processes=self.config.workers,
            initializer=init_worker,
            initargs=(adapter_type, adapter_kwargs),
        ) as pool:

            # -------------------------------------------------------------
            # Dimension 1: Accuracy Benchmark (Includes /samples/4k/course)
            # -------------------------------------------------------------
            if verbose:
                print("\n[1/6] Running Ground-Truth Accuracy Benchmark (including Course)...")

            all_acc_samples = self.dataset.get_accuracy_samples()
            acc_samples = self._select_stratified_samples(
                all_acc_samples, self.config.sample_limit_accuracy
            )

            file_paths = [str(s.file_path) for s in acc_samples]
            calc_outputs = pool.map(run_calc_path_timed, file_paths)

            failed_beatmaps: List[Dict[str, str]] = []
            valid_acc_samples: List[BenchmarkSample] = []
            valid_raw_preds: List[float] = []
            valid_calc_times: List[float] = []

            for s, (diff, dt_ms, err_msg) in zip(acc_samples, calc_outputs):
                if diff is None:
                    failed_beatmaps.append({
                        "name": s.name,
                        "path": str(s.file_path),
                        "expected": str(s.expected),
                        "pattern": s.pattern,
                        "error": err_msg or "Execution error / calculation failed",
                    })
                else:
                    valid_acc_samples.append(s)
                    valid_raw_preds.append(diff)
                    valid_calc_times.append(dt_ms)

            if failed_beatmaps and verbose:
                print(f"  [!] Excluded {len(failed_beatmaps)} beatmap(s) due to calculation errors/crashes:")
                for fb in failed_beatmaps[:5]:
                    print(f"      - {fb['name']} (Expected: {fb['expected']}, Pattern: {fb['pattern']}): {fb['error']}")
                if len(failed_beatmaps) > 5:
                    print(f"      ... and {len(failed_beatmaps) - 5} more")

            acc_samples = valid_acc_samples
            raw_predictions = valid_raw_preds
            calc_times_ms = valid_calc_times

            acc_timings = [
                (calc_times_ms[i], s.duration_seconds)
                for i, s in enumerate(acc_samples)
            ]

            acc_result = evaluate_accuracy(
                samples=acc_samples,
                raw_predictions=raw_predictions,
                scale_type=self.adapter.scale_type,
            )

            if verbose:
                print(
                    f"  -> Calibrated ng-RMSE: {acc_result.global_rmse:.4f} | "
                    f"Spearman rho: {acc_result.spearman_rho:.4f} | "
                    f"WTD: {acc_result.mean_wtd:.4f}"
                )
                if acc_result.calibration.macro_alerts:
                    print(f"  [!] Macro Inversion Warnings ({len(acc_result.calibration.macro_alerts)} detected):")
                    for alert in acc_result.calibration.macro_alerts[:3]:
                        print(f"      {alert}")

            # Store baseline difficulties by map name
            base_diff_map: Dict[str, float] = {
                s.name: raw_predictions[i] for i, s in enumerate(acc_samples)
            }

            # -------------------------------------------------------------
            # Dimensions 2 & 3: Anti-Rate-Inversion & Local Continuity
            # (Strictly EXCLUDES /samples/4k/course and /samples/4k/ln)
            # -------------------------------------------------------------
            if verbose:
                print("\n[2/6 & 3/6] Running Rate Scaling & Continuity Suite (excluding Course & LN)...")

            acc_pert_candidates = [s for s in acc_samples if not s.is_course and not s.is_ln]
            all_pert_samples = acc_pert_candidates if acc_pert_candidates else self.dataset.get_perturbation_samples()
            pert_samples = self._select_stratified_samples(
                all_pert_samples, self.config.sample_limit_perturbation
            )

            # Pre-read beatmap contents for perturbation samples
            beatmap_contents: Dict[str, str] = {}
            for s in pert_samples:
                with open(s.file_path, "r", encoding="utf-8", errors="replace") as fp:
                    beatmap_contents[s.name] = fp.read()

            rate_curves: Dict[str, List[Tuple[float, float]]] = {}

            if self.config.adaptive_rate:
                # ---------------------------------------------------------
                # Adaptive two-phase rate sweep:
                #   Phase 1: coarse scan (step = 0.1) over the full range
                #   Phase 2: fine refinement only on suspicious maps/intervals
                # ---------------------------------------------------------
                coarse_rates = np.arange(
                    self.config.rate_min,
                    self.config.rate_max + 1e-5,
                    0.1,
                ).round(3).tolist()
                coarse_tasks = [
                    (s.name, float(r), beatmap_contents[s.name])
                    for s in pert_samples
                    for r in coarse_rates
                ]
                if verbose:
                    print(
                        f"  -> [Adaptive] Phase 1 coarse scan: {len(pert_samples)} maps x "
                        f"{len(coarse_rates)} rates ({len(coarse_tasks)} evaluations)..."
                    )
                coarse_outputs = pool.map(
                    run_calc_rate_job,
                    coarse_tasks,
                    chunksize=max(1, len(coarse_tasks) // (self.config.workers * 4)),
                )
                for map_name, r, diff in coarse_outputs:
                    if diff is not None:
                        rate_curves.setdefault(map_name, []).append((r, diff))
                for map_name in rate_curves:
                    rate_curves[map_name].sort(key=lambda pt: pt[0])

                # Phase 2: refine suspicious intervals at the fine step
                fine_step = min(self.config.rate_step, self.config.rate_refine_step)
                refine_tasks = []
                refined_maps = 0
                for s in pert_samples:
                    intervals = _find_suspicious_intervals(rate_curves.get(s.name))
                    if not intervals:
                        continue
                    refined_maps += 1
                    new_rates = set()
                    for (a, b) in intervals:
                        r = a + fine_step
                        while r < b - 1e-6:
                            new_rates.add(round(float(r), 3))
                            r += fine_step
                    for r in sorted(new_rates):
                        refine_tasks.append((s.name, r, beatmap_contents[s.name]))

                if refine_tasks:
                    if verbose:
                        print(
                            f"  -> [Adaptive] Phase 2 refinement: {refined_maps} suspicious maps, "
                            f"{len(refine_tasks)} extra evaluations (step={fine_step})..."
                        )
                    refine_outputs = pool.map(
                        run_calc_rate_job,
                        refine_tasks,
                        chunksize=max(1, len(refine_tasks) // (self.config.workers * 4)),
                    )
                    for map_name, r, diff in refine_outputs:
                        if diff is not None:
                            rate_curves.setdefault(map_name, []).append((r, diff))
                    for map_name in rate_curves:
                        rate_curves[map_name].sort(key=lambda pt: pt[0])
            else:
                # Plain uniform rate sweep (legacy behavior / quick mode)
                rates = np.arange(
                    self.config.rate_min,
                    self.config.rate_max + 1e-5,
                    self.config.rate_step,
                ).round(3).tolist()

                rate_tasks = []
                for s in pert_samples:
                    c_str = beatmap_contents[s.name]
                    for r in rates:
                        rate_tasks.append((s.name, float(r), c_str))

                if verbose:
                    print(f"  -> Evaluating {len(pert_samples)} maps across {len(rates)} rates ({len(rate_tasks)} evaluations)...")

                rate_outputs = pool.map(run_calc_rate_job, rate_tasks, chunksize=max(1, len(rate_tasks) // (self.config.workers * 4)))

                for map_name, r, diff in rate_outputs:
                    if diff is not None:
                        rate_curves.setdefault(map_name, []).append((r, diff))

                for map_name in rate_curves:
                    rate_curves[map_name].sort(key=lambda pt: pt[0])

            inv_result = evaluate_rate_inversion(rate_curves)
            cont_result = evaluate_continuity(rate_curves)

            if verbose:
                print(
                    f"  -> Inversion Frequency (IF): {inv_result.inversion_frequency*100:.2f}% | "
                    f"IDA Deficit: {inv_result.auc_deficit:.5f} | "
                    f"Max Drop: {inv_result.max_relative_drop*100:.2f}%"
                )
                print(
                    f"  -> Cliff Events: {cont_result.cliff_events_count} | "
                    f"Max Lipschitz: {cont_result.max_lipschitz:.1f} | "
                    f"Smoothness Score: {cont_result.smoothness_score:.1f}/100"
                )

            # -------------------------------------------------------------
            # Dimension 4: Anti-Jitter Benchmark (Strictly EXCLUDES Course & LN)
            # -------------------------------------------------------------
            if verbose:
                print("\n[4/6] Running Anti-Jitter Timing Perturbation Benchmark (excluding Course & LN)...")

            jitter_tasks = []
            jitter_sample_pairs = []

            for s in pert_samples:
                c_str = beatmap_contents[s.name]
                orig_d = base_diff_map.get(s.name)
                if orig_d is None:
                    continue
                jitter_tasks.append((s.name, c_str, self.config.seed))
                jitter_sample_pairs.append((s, orig_d))

            jitter_outputs = pool.map(run_calc_jitter_job, jitter_tasks)
            jitter_orig_diffs = []
            jitter_diffs = []
            jitter_map: Dict[str, Tuple[float, float]] = {}
            for (s, orig_d), (_, j_diff) in zip(jitter_sample_pairs, jitter_outputs):
                if j_diff is not None:
                    jitter_orig_diffs.append(orig_d)
                    jitter_diffs.append(j_diff)
                    jitter_map[s.name] = (orig_d, j_diff)

            jitter_result = evaluate_jitter(jitter_orig_diffs, jitter_diffs)

            if verbose:
                print(
                    f"  -> Jitter Self-RMSE: {jitter_result.jitter_rmse:.5f} | "
                    f"MAPE: {jitter_result.mape:.3f}% | "
                    f"Max Delta: {jitter_result.max_absolute_delta:.5f}"
                )

            # -------------------------------------------------------------
            # Dimension 5: Anti-Short LN Benchmark (Strictly EXCLUDES Course & LN)
            # -------------------------------------------------------------
            if verbose:
                print("\n[5/6] Running Anti-Short LN Release Artifact Benchmark (excluding Course & LN)...")

            short_ln_tasks = []
            sln_sample_pairs = []

            for s in pert_samples:
                c_str = beatmap_contents[s.name]
                orig_d = base_diff_map.get(s.name)
                if orig_d is None:
                    continue
                short_ln_tasks.append((s.name, c_str, self.config.seed))
                sln_sample_pairs.append((s, orig_d))

            sln_outputs = pool.map(run_calc_short_ln_job, short_ln_tasks)
            sln_orig_diffs = []
            sln_diffs = []
            sln_map: Dict[str, Tuple[float, float]] = {}
            for (s, orig_d), (_, s_diff) in zip(sln_sample_pairs, sln_outputs):
                if s_diff is not None:
                    sln_orig_diffs.append(orig_d)
                    sln_diffs.append(s_diff)
                    sln_map[s.name] = (orig_d, s_diff)

            # Check if LN is excluded by configuration/filters
            ln_excluded = False
            if self.config.exclude_patterns and any("ln" in p.lower() for p in self.config.exclude_patterns):
                ln_excluded = True
            elif self.config.patterns and not any("ln" in p.lower() for p in self.config.patterns):
                ln_excluded = True
            elif self.config.filter_preset and preset_excludes_pattern(
                self.config.filter_preset, "ln"
            ):
                ln_excluded = True

            # If not excluded, probe whether algorithm performs brute-force LN-to-tap stripping (HO probe)
            is_brute_force_tap = False
            if not ln_excluded:
                ln_samples = [s for s in self.dataset.samples if s.is_ln]
                if ln_samples:
                    probe_samples = ln_samples[:5]
                    ho_tasks = []
                    for s in probe_samples:
                        bm = Beatmap.parse(s.file_path)
                        bm_ho = apply_hold_off(bm)
                        ho_tasks.append((str(s.file_path), bm_ho.serialize()))

                    ho_outputs = pool.map(run_calc_ho_probe_job, ho_tasks)
                    ho_deltas = [
                        abs(d_orig - d_ho) / max(0.01, d_orig) * 100.0
                        for d_orig, d_ho in ho_outputs
                        if d_orig is not None and d_ho is not None
                    ]
                    mean_ho_delta_pct = sum(ho_deltas) / len(ho_deltas) if ho_deltas else 100.0
                    if mean_ho_delta_pct < 2.0:
                        is_brute_force_tap = True
                        if verbose:
                            print(f"  [!] Brute-Force Tap Stripping Detected: LN vs HO delta = {mean_ho_delta_pct:.2f}% (< 2.0%)")

            sln_result = evaluate_short_ln(
                sln_orig_diffs,
                sln_diffs,
                is_brute_force_tap=is_brute_force_tap,
                ln_excluded=ln_excluded,
            )

            if verbose:
                if ln_excluded:
                    print(f"  [!] LN dataset excluded by tester / preset: Anti-Short LN assigned 0.0 pts")
                elif is_brute_force_tap:
                    print(f"  [!] Algorithm strips LN to tap (LN-deficient): Anti-Short LN assigned 0.0 pts")
                print(
                    f"  -> Short-LN Self-RMSE: {sln_result.short_ln_rmse:.5f} | "
                    f"Inflation (ITR): {sln_result.inflation_tendency_ratio:+.3f}% | "
                    f"Grade: {sln_result.immunity_grade}"
                )

        # -------------------------------------------------------------
        # Dimension 6: Algorithmic Computation Speed Benchmark (ms/s)
        # -------------------------------------------------------------
        speed_result = evaluate_speed(acc_timings)
        if verbose:
            print("\n[6/6] Computing Algorithmic Calculation Speed (ms/s)...")
            print(
                f"  -> Average Speed: {speed_result.average_speed_ms_per_s:.3f} ms/s | "
                f"Median: {speed_result.median_speed_ms_per_s:.3f} ms/s | "
                f"Realtime Factor: {speed_result.realtime_factor:.1f}x | "
                f"Score: {speed_result.speed_score:.1f}/100"
            )

        # SP provenance override: fast drop-in replacements (e.g. sunny-rs)
        # inherit the SP dimension from the original algorithm's run so the
        # score keeps reflecting the original's real speed.
        if self.config.sp_source:
            overridden = _load_speed_result(self.config.sp_source)
            if overridden is not None:
                speed_result = overridden
                if verbose:
                    print(
                        f"  [i] SP inherited from '{self.config.sp_source}' "
                        f"(original algorithm): {speed_result.speed_score:.1f}/100 "
                        f"@ {speed_result.average_speed_ms_per_s:.3f} ms/s"
                    )
            elif verbose:
                print(
                    f"  [!] sp_source '{self.config.sp_source}' not found/readable; "
                    f"keeping measured SP."
                )

        # -------------------------------------------------------------
        # Synthesize Master Score (0 - 100 points)
        # -------------------------------------------------------------
        score_breakdown = calculate_cab_score(
            accuracy=acc_result,
            inversion=inv_result,
            continuity=cont_result,
            jitter=jitter_result,
            short_ln=sln_result,
            speed=speed_result,
        )

        t_end = time.time()
        elapsed = t_end - t_start

        master_result = BenchmarkRunResult(
            algorithm_name=self.adapter.name,
            scale_type=self.adapter.scale_type,
            config=self.config,
            accuracy=acc_result,
            inversion=inv_result,
            continuity=cont_result,
            jitter=jitter_result,
            short_ln=sln_result,
            speed=speed_result,
            score=score_breakdown,
            rate_curves=rate_curves,
            duration_seconds=elapsed,
            filter_summary=self.config.get_filter_summary(),
            sample_stats=self.dataset.get_stats_summary(),
            tier_id=TIER_ALL,
            tier_name=TIER_DEFINITIONS[TIER_ALL].name,
            failed_beatmaps=failed_beatmaps,
        )

        tier_mode = (self.config.tier_mode or "multi").lower().strip()
        if tier_mode in ("multi", "all-tiers", "4-tiers"):
            tier_results: Dict[str, BenchmarkRunResult] = {TIER_ALL: master_result}

            for t_id in (TIER_NUMBER, TIER_GREEK, TIER_INHUMAN):
                t_def = TIER_DEFINITIONS[t_id]
                tier_res = self._evaluate_tier_slice(
                    tier=t_def,
                    acc_samples=acc_samples,
                    raw_predictions=raw_predictions,
                    acc_timings=acc_timings,
                    pert_samples=pert_samples,
                    rate_curves=rate_curves,
                    jitter_map=jitter_map,
                    sln_map=sln_map,
                    is_brute_force_tap=is_brute_force_tap,
                    ln_excluded=ln_excluded,
                    elapsed=elapsed,
                )
                tier_results[t_id] = tier_res

            if verbose:
                print(f"\n============================================================")
                print(f"BENCHMARK COMPLETE (Elapsed: {elapsed:.2f}s)")
                print(f"Total Standardized CAB-Score (All Range): {score_breakdown.total_score:.1f} / 100")
                print(f"============================================================")
                for dim, pts in score_breakdown.dimension_scores.items():
                    w_pct = score_breakdown.dimension_weights.get(dim, 0.0) * 100
                    print(f"  * {dim:28s}: {pts:5.1f} / 100 pts (Weight: {w_pct:2.0f}%)")

                print("\n" + "=" * 105)
                print("CAB-VA MULTI-TIER BENCHMARK SUMMARY (4 TIERS)")
                print(f"Target Algorithm: {self.adapter.name} | Scale: {self.adapter.scale_type} | Mode: {self.config.mode.upper()}")
                print("=" * 105)
                print(f"{'Tier Name':<20} {'Range':<15} {'Maps':<6} {'CAB-Score':<11} {'ACC(57.14%*)':<13} {'AIN(20%*)':<10} {'ASLN(21.43%*)':<14} {'CT(10%*)':<9} {'SPD(7.14%*)':<12} {'AJ(14.29%*)':<12}")
                print("-" * 105)
                for t_id in DEFAULT_TIERS:
                    tr = tier_results[t_id]
                    if t_id == TIER_ALL:
                        range_str = ">= -2.5"
                    elif t_id == TIER_NUMBER:
                        range_str = "[-2.5, 10.5)"
                    elif t_id == TIER_GREEK:
                        range_str = "[10.5, 15.5]"
                    else:
                        range_str = "> 15.5"
                    map_count = len(tr.accuracy.calibrated_predictions)
                    if tr.score.is_out_of_domain:
                        score_str = "N/A"
                        acc_str = "N/A"
                        ain_str = "-"
                        asln_str = "N/A"
                        ct_str = "-"
                        spd_str = "N/A"
                        aj_str = "N/A"
                    else:
                        score_str = f"{tr.score.total_score:5.1f}/100"
                        acc_str = f"{tr.score.s_accuracy:5.1f}"
                        ain_str = f"{tr.score.s_inversion:5.1f}" if tr.score.s_inversion is not None else "-"
                        asln_str = f"{tr.score.s_short_ln:5.1f}"
                        ct_str = f"{tr.score.s_continuity:5.1f}" if tr.score.s_continuity is not None else "-"
                        spd_str = f"{tr.score.s_speed:5.1f}"
                        aj_str = f"{tr.score.s_jitter:5.1f}"

                    print(
                        f"{tr.tier_name:<20} {range_str:<15} {map_count:<6} "
                        f"{score_str:<11} "
                        f"{acc_str:<10} "
                        f"{ain_str:<10} "
                        f"{asln_str:<11} "
                        f"{ct_str:<9} "
                        f"{spd_str:<9} "
                        f"{aj_str:<8}"
                    )
                print("=" * 105)
                print("* Note: Sub-tiers (Number, Greek, Inhuman) evaluate 4 dimensions (ACC 57.14%, ASLN 21.43%, SPD 7.14%, AJ 14.29%).")
                print("        Anti-Rate-Inversion (AIN) & Local Continuity (CT) are evaluated exclusively in All Range (20% & 10%).\n")

            return MultiTierBenchmarkResult(
                algorithm_name=self.adapter.name,
                scale_type=self.adapter.scale_type,
                config=self.config,
                duration_seconds=elapsed,
                tier_results=tier_results,
                master_result=master_result,
            )

        # Single tier requested
        single_tier_def = self.config.get_tier_list()[0]
        if single_tier_def.tier_id == TIER_ALL:
            return master_result

        return self._evaluate_tier_slice(
            tier=single_tier_def,
            acc_samples=acc_samples,
            raw_predictions=raw_predictions,
            acc_timings=acc_timings,
            pert_samples=pert_samples,
            rate_curves=rate_curves,
            jitter_map=jitter_map,
            sln_map=sln_map,
            is_brute_force_tap=is_brute_force_tap,
            ln_excluded=ln_excluded,
            elapsed=elapsed,
        )
