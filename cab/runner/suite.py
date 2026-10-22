"""CAB-VA Benchmark Suite Orchestrator.

Coordinates execution across all five core benchmark dimensions:
1. Ground-Truth Accuracy & Calibrated ND-RMSE (includes course)
2. Anti-Rate-Inversion (strictly excludes course)
3. Local Continuity & Cliffing (strictly excludes course)
4. Anti-Jitter Robustness (strictly excludes course)
5. Anti-Short LN Robustness (strictly excludes course)
"""

from __future__ import annotations

import math
import multiprocessing as mp
import time
import warnings
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
from cab.metrics.abstention import (
    AbstentionDeclaration,
    ScopeReport,
    apply_declarations,
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
    run_calc_arrangement_probe_job,
    run_calc_content,
    run_calc_ho_probe_job,
    run_calc_jitter_job,
    run_calc_path,
    run_calc_path_timed,
    run_calc_rate_job,
    run_calc_rate_batch,
    run_calc_short_ln_job,
    run_measure_overhead,
    run_ping,
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
    # Accuracy-stage samples that produced ``accuracy.calibrated_predictions``.
    # Strictly ordered 1:1 with that list, so index ``i`` of the two always
    # describes the same beatmap. Consumed by the per-map predictions CSV export.
    # May be empty for legacy/unpickled results.
    samples: List[BenchmarkSample] = field(default_factory=list)
    # The algorithm's pre-calibration readings, index-aligned with ``samples``.
    # ``accuracy.calibrated_predictions`` holds the post-calibration values; both
    # are exported so the per-map CSV can show raw and calibrated side by side.
    raw_predictions: List[float] = field(default_factory=list)
    # Per-map dynamic-probe ledgers: name -> (original, perturbed) difficulty.
    # Exported as jitter_ledger.csv / short_ln_ledger.csv so those dimensions
    # can be audited per beatmap instead of only through a top-10 list.
    jitter_ledger: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    sln_ledger: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    # Per-map r* actually used by AJ / ASLN.  Targets that fall outside the
    # sampled rate grid are resolved by on-demand probes (0.8/0.7, 3.0/4.0);
    # those readings never reach ``rate_curves``, so the resolved values have to
    # travel with the result -- otherwise the ledger re-inverts, gets None, and
    # writes a blank for exactly the charts that needed the extra work.
    rstar_jitter: Dict[str, float] = field(default_factory=dict)
    rstar_sln: Dict[str, float] = field(default_factory=dict)
    # Per-map, per-rate 1 ms quantization noise floors from the Phase-3 audit.
    # The audit re-runs the algorithm under floor() rounding, so it cannot be
    # reproduced offline -- it has to travel with the ledger for AIN to rescore
    # exactly.
    rate_noise_floors: Dict[str, Dict[float, float]] = field(default_factory=dict)
    # Declared difficulty scope (a.k.a. abstention). ``scope_report`` records
    # what the algorithm declared it does not attempt; it never changes any
    # headline metric. ``in_scope_accuracy`` is a *secondary* Accuracy reading
    # computed over the kept beatmaps only, with its own freshly fitted anchors,
    # so a specialised algorithm can show how it does inside its own slice.
    scope_report: Optional["ScopeReport"] = None
    in_scope_accuracy: Optional[AccuracyResult] = None
    # Post-calibration predictions restricted to ``scope_report.in_scope_samples``,
    # index-aligned with that list. NaN-free; empty when no scope is declared.
    in_scope_predictions: List[float] = field(default_factory=list)


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


#: Algorithms that may legitimately inherit each other's speed reading.
#: ``sunny-rs`` is a drop-in Rust port of ``Star-Rating-Rebirth``; its SP is
#: inherited from the Python original's archive on purpose.
_SP_SOURCE_EQUIVALENTS: Dict[str, set] = {
    "sunny": {"sunny", "sunny-rs", "star-rating-rebirth", "star rating rebirth"},
    "sunny-rs": {"sunny", "sunny-rs", "star-rating-rebirth", "star rating rebirth"},
    "arily": {"arily"},
}


def _sp_source_compatible(expected: str, archived: str) -> bool:
    """Whether ``expected`` may inherit an archive labelled ``archived``."""
    import os as _os

    exp = expected.strip().lower()
    arc = archived.strip().lower()
    if not arc or exp == arc:
        return True
    family = exp.split("-")[0].replace("_", " ").strip()
    if family and family in arc:
        return True
    allowed = _SP_SOURCE_EQUIVALENTS.get(exp, set())
    if arc in allowed:
        return True
    # Explicit escape hatch for a hand-curated archive.
    return _os.environ.get("CAB_SP_SOURCE_FORCE", "").strip() in ("1", "true", "yes")


def _load_speed_result(source_dir: str, expected_algorithm: Optional[str] = None) -> Optional[SpeedResult]:
    """Load a SpeedResult from another run's exported JSON.

    ``source_dir`` may be either a results directory containing
    ``cab_benchmark_result.json`` (or ``all/cab_benchmark_result.json``) or a
    direct path to such a JSON file. Returns None when nothing usable is found,
    so callers can fall back to the freshly measured speed.

    ``expected_algorithm`` guards against inheriting another algorithm's speed
    reading: pointing ``--sp-source`` at the wrong directory used to be accepted
    silently (CODE_REVIEW P1-20).
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
            if expected_algorithm:
                archived_algorithm = str(data.get("algorithm") or "")
                if archived_algorithm and not _sp_source_compatible(expected_algorithm, archived_algorithm):
                    warnings.warn(
                        f"SP archive {cand} belongs to {archived_algorithm!r}, not "
                        f"{expected_algorithm!r}; refusing to inherit its speed reading "
                        "(set CAB_SP_SOURCE_FORCE=1 to override)",
                        RuntimeWarning,
                        stacklevel=2,
                    )
                    continue
            sp = data.get("dimension_6_speed")
            if not isinstance(sp, dict):
                continue
            # An archived reading with no evaluated sample must not be inherited:
            # it would hand the consuming algorithm a free "perfect" SP.
            try:
                _arch_count = int(sp.get("evaluated_count", 0) or 0)
            except (TypeError, ValueError):
                _arch_count = 0
            if _arch_count <= 0:
                warnings.warn(
                    f"SP archive {cand} has evaluated_count={_arch_count}; ignoring it",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            # Cross-machine archives carry the baseline ratio: re-score it with
            # the current curve (the stored absolute score belongs to whatever
            # curve was active when the archive was written).
            _arch_ratio = sp.get("baseline_ratio")
            if _arch_ratio is not None:
                from cab.metrics.speed_metric import calculate_speed_score_relative

                _arch_score = calculate_speed_score_relative(float(_arch_ratio))
            elif sp.get("speed_score") is not None:
                _arch_score = float(sp["speed_score"])
            else:
                # Missing score + missing ratio: not a usable measurement.
                warnings.warn(
                    f"SP archive {cand} has neither speed_score nor baseline_ratio; ignoring it",
                    RuntimeWarning,
                    stacklevel=2,
                )
                continue
            _arch_rf = sp.get("realtime_factor")
            return SpeedResult(
                average_speed_ms_per_s=float(sp.get("average_speed_ms_per_s", 0.0)),
                median_speed_ms_per_s=float(sp.get("median_speed_ms_per_s", 0.0)),
                realtime_factor=float(_arch_rf) if _arch_rf is not None else None,
                speed_score=_arch_score,
                evaluated_count=_arch_count,
                sample_speeds=[],
                p95_speed_ms_per_s=float(sp.get("p95_speed_ms_per_s", 0.0)),
                max_speed_ms_per_s=float(sp.get("max_speed_ms_per_s", 0.0)),
                # Carried through so a provenance file stays self-describing:
                # a non-zero value means the archived reading already had the
                # compatibility-layer cost removed.
                platform_overhead_ms=float(sp.get("platform_overhead_ms", 0.0) or 0.0),
                baseline_ratio=(
                    float(sp["baseline_ratio"])
                    if sp.get("baseline_ratio") is not None
                    else None
                ),
                baseline_ms_per_s=(
                    float(sp["baseline_ms_per_s"])
                    if sp.get("baseline_ms_per_s") is not None
                    else None
                ),
                baseline_algorithm=str(sp.get("baseline_algorithm", "") or ""),
            )
        except Exception:  # noqa: BLE001
            continue
    return None


def _build_rate_grid(
    rate_min: float,
    rate_max: float,
    step: float,
    rate_floor: float | None = None,
) -> list[float]:
    """Rate sampling grid over ``[rate_min, rate_max]``, always ending at ``rate_max``.

    With a tenth-sized step the ladder is anchored to whole decimals
    (1.0, 1.1, ... 2.0) rather than offset by ``rate_min``.  The old
    ``arange(1.01, 2.0 + 1e-5, 0.1)`` produced 1.01 / 1.11 / 1.21 / ... and,
    because ``1.01 + 0.1 * 9 == 1.91``, **never actually sampled 2.0** -- the
    configured ``rate_max`` was unreachable in standard and quick mode.

    ``rate_min`` itself is included, so the sweep starts at true 1.0x (the
    unscaled map).  That origin anchor is what makes "drops the moment you
    speed it up" observable: on the 856-chart batch ~63% of the worst-case
    drawdowns peak at the very first step.
    """
    rates: set[float] = {round(float(rate_min), 3)}

    # Sub-unity probes: a finer 0.05 ladder, because D(r) is nearly flat just
    # below 1.0 and a coarse grid would make the r* inversion interpolate
    # across too large a gap.
    if rate_floor is not None and rate_floor < rate_min - 1e-9:
        r_floor = float(rate_floor)
        while r_floor < rate_min - 1e-9:
            rates.add(round(r_floor, 3))
            r_floor += 0.05

    if abs(step - 0.1) < 1e-9:
        # Anchor to the decade ladder: first tenth at or above rate_min.
        r = math.ceil(rate_min * 10.0 - 1e-9) / 10.0
    else:
        r = rate_min + step
    while r <= rate_max + 1e-9:
        rates.add(round(float(r), 3))
        r += step

    # Guarantee the configured upper bound is actually evaluated.
    if max(rates) < rate_max - 1e-9:
        rates.add(round(float(rate_max), 3))

    return sorted(rates)


def _invert_rate_curve(curve, target: float) -> Optional[float]:
    """Clock rate that would reproduce ``target`` difficulty on ``curve``.

    Linear interpolation over the saved ``D(r)`` samples.  Returns ``None`` when
    the target falls outside the sampled range or the curve is not monotonic
    across it (non-monotonic algorithms lose some charts to this).
    """
    points = sorted((float(r), float(d)) for r, d in curve)
    for (r0, d0), (r1, d1) in zip(points, points[1:]):
        if d0 <= target <= d1 or d1 <= target <= d0:
            if abs(d1 - d0) < 1e-12:
                return r0
            return r0 + (target - d0) / (d1 - d0) * (r1 - r0)
    return None


PROBE_COVERAGE_FLOOR = 0.95


def _check_probe_coverage(
    label: str,
    evaluated: int,
    expected: int,
    dropped: Sequence[str],
    verbose: bool = False,
) -> None:
    """Surface probe charts that produced no reading.

    A dropped chart used to be invisible: the dimension simply scored a smaller
    sample, so an adapter whose persistent server kept dying (Dan-Overlay under
    Wine on 2026-10-21) looked the same as a healthy one, and the gap was only
    inferable from a smaller ``evaluated`` count.  Warn below
    ``PROBE_COVERAGE_FLOOR`` and always name the charts.
    """
    if expected <= 0 or evaluated >= expected:
        return
    coverage = evaluated / expected
    detail = ", ".join(list(dropped)[:5])
    if len(dropped) > 5:
        detail += f", ... (+{len(dropped) - 5})"
    message = (
        f"{label} probe covered {evaluated}/{expected} charts "
        f"({coverage:.1%}); dropped: {detail or 'unknown'}"
    )
    if coverage < PROBE_COVERAGE_FLOOR:
        warnings.warn(message + " -- dimension is degraded", RuntimeWarning, stacklevel=2)
    if verbose:
        print(f"  [!] {message}")


def _is_speed_probe(sample: BenchmarkSample) -> bool:
    """True for the community dan courses used by the speed probe.

    The set is exactly 33 charts: the 15 ``4K LN Dan Courses v2`` charts plus 18
    ``Dan ~ REFORM ~`` charts.  The duplicate ND-6 entry from 2nd Pack and the
    two FINAL charts are excluded, matching the curated list.

    Why these: every one is a marathon (405-727 s, median 491 s).  The previous
    probe reused the accuracy timings across all 856 charts, where a 36-second
    chart could double its own ms/s from a few milliseconds of scheduling noise.
    """
    # Only the bundled course charts: the ln/ folder also holds 63 single-chart
    # cuts from the same "4K LN Dan Courses v2 ~ Stage N Map Pack ~" series,
    # which would otherwise be swept in by a bare name match.
    if (sample.pattern or "").lower() != "course":
        return False

    name = sample.name
    if "4K LN Dan Courses v2" in name:
        return True
    if "Dan ~ REFORM ~" not in name:
        return False
    if "FINAL" in name:
        return False
    if "2nd Pack" in name and "~ 6th ~" in name:
        return False
    return True


def _find_suspicious_intervals(curve) -> list:
    """Detect rate intervals [r_k, r_{k+1}] that warrant a finer sweep.

    A pair is suspicious when:
      (1) the difficulty drops between adjacent rates (potential inversion),
      (2) the local gradient spikes strongly above the map's median gradient
          (potential cliff), or
      (3) the local gradient stalls/decelerates sharply below the map's median
          positive gradient (where an intra-interval peak-and-dip can hide).
    Used by the adaptive two-phase rate sweep to concentrate fine sampling
    where problems actually are.
    """
    if not curve or len(curve) < 3:
        return []
    rs = [pt[0] for pt in curve]
    ds = [pt[1] for pt in curve]
    suspicious = set()

    # 1) Non-monotonic adjacent pairs (plus immediate neighbors to capture true peak/trough)
    for k in range(len(ds) - 1):
        if ds[k + 1] < ds[k] - 1e-9:
            suspicious.add((rs[k], rs[k + 1]))
            if k > 0:
                suspicious.add((rs[k - 1], rs[k]))
            if k + 1 < len(rs) - 1:
                suspicious.add((rs[k + 1], rs[k + 2]))

    # 2) Gradient outliers (spikes or sharp stagnation) vs. the map's median gradient
    grads = [
        (ds[k + 1] - ds[k]) / max(rs[k + 1] - rs[k], 1e-9)
        for k in range(len(ds) - 1)
    ]
    abs_grads = [abs(g) for g in grads]
    med = float(np.median(abs_grads)) if abs_grads else 0.0
    pos_grads = [g for g in grads if g > 1e-6]
    med_pos = float(np.median(pos_grads)) if pos_grads else 0.0
    for k, g in enumerate(grads):
        if abs(g) > max(1.75 * med, 1e-6):
            suspicious.add((rs[k], rs[k + 1]))
            if k > 0:
                suspicious.add((rs[k - 1], rs[k]))
            if k + 1 < len(rs) - 1:
                suspicious.add((rs[k + 1], rs[k + 2]))
        elif med_pos > 1e-3 and g < 0.20 * med_pos:
            suspicious.add((rs[k], rs[k + 1]))
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

        # Curve/ledger maps are keyed by chart name.  Duplicate names would
        # silently share a rate curve (and therefore a whole dimension) between
        # two different charts, so reject them up front instead.
        names = [s.name for s in self.dataset.samples]
        if len(names) != len(set(names)):
            from collections import Counter

            dupes = [n for n, c in Counter(names).items() if c > 1]
            raise ValueError(
                f"dataset contains duplicate chart names ({dupes[:5]}); "
                "per-map curves would be shared between different charts"
            )

    def _map(self, pool, fn, items, chunksize: Optional[int] = None, timeout: Optional[float] = None):
        """Ordered ``pool.map`` with a per-task timeout and fail-fast semantics.

        ``multiprocessing.Pool.map`` does **not** raise when a worker dies: a
        process killed by the OOM killer, or one that segfaults inside a native
        extension, simply never returns its result and the whole benchmark
        hangs without a single log line (CODE_REVIEW P0-05).  ``apply_async`` +
        ``get(timeout=...)`` turns that into a diagnosable error, and also gives
        every individual chart a hard wall-clock budget (P1-16).

        Result order is identical to ``pool.map`` (submission order).
        """
        task_list = list(items)
        if not task_list:
            return []
        per_task = float(timeout if timeout is not None else self.config.task_timeout_seconds)

        pending = [pool.apply_async(fn, (item,)) for item in task_list]
        results: list = []
        try:
            for idx, async_result in enumerate(pending):
                try:
                    results.append(async_result.get(timeout=per_task))
                except (mp.TimeoutError, TimeoutError):
                    pool.terminate()
                    raise RuntimeError(
                        f"worker task {idx + 1}/{len(task_list)} ({getattr(fn, '__name__', fn)}) "
                        f"exceeded {per_task:g}s -- worker probably died (OOM/SIGSEGV) "
                        "or the algorithm hung; pool terminated instead of hanging forever"
                    ) from None
        except BaseException:
            for leftover in pending[len(results):]:
                try:
                    leftover.cancel()
                except Exception:  # noqa: BLE001
                    pass
            raise
        return results

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
                    # Needed inside the worker: the compatibility-layer baseline
                    # must be measured through the same persistent server the
                    # speed samples go through.
                    "overhead_probe_cmd": getattr(
                        self.adapter, "overhead_probe_cmd", None
                    ),
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

    def _evaluate_scope_accuracy(
        self,
        acc_samples: Sequence[BenchmarkSample],
        raw_predictions: Sequence[float],
    ) -> Tuple[Optional[ScopeReport], Optional[AccuracyResult], List[float]]:
        """Compute the secondary in-scope Accuracy reading.

        The declared scope never changes the headline metrics. This helper only
        produces the *supplementary* reading over the kept beatmaps, re-fitting
        the calibration anchors on that subset so a specialised algorithm is
        judged on its own terms inside the slice it targets.

        Returns ``(scope_report, in_scope_accuracy, in_scope_predictions)``.
        All three are empty/``None`` when no scope is declared, and the Accuracy
        reading is ``None`` when the declaration leaves fewer than two charts
        (too few to fit anchors or compute an RMSE).
        """
        declarations = list(getattr(self.config, "scope_declarations", []) or [])
        if not declarations:
            return None, None, []

        report = apply_declarations(list(acc_samples), declarations)

        kept_samples = report.in_scope_samples
        if len(kept_samples) < 2:
            return report, None, []

        # raw_predictions is index-aligned with acc_samples; recover the kept
        # subset's raw readings by identity of list position.
        index_of = {id(s): i for i, s in enumerate(acc_samples)}
        kept_raw = [float(raw_predictions[index_of[id(s)]]) for s in kept_samples]

        in_scope_acc = evaluate_accuracy(
            samples=kept_samples,
            raw_predictions=kept_raw,
            scale_type=self.adapter.scale_type,
        )
        return report, in_scope_acc, list(in_scope_acc.calibrated_predictions)

    def _resolve_rstar(
        self,
        pool,
        curves: Dict[str, List[Tuple[float, float]]],
        targets: Dict[str, float],
        contents: Dict[str, str],
        label: str = "r*",
    ) -> Dict[str, float]:
        """Map chart name -> r*, probing outside the sampled rate grid if needed.

        ``_invert_rate_curve`` can only answer inside ``[D(rate_floor), D(rate_max)]``.
        Everything outside used to become ``None`` and silently leave the median
        (MinaCalc-0.70 lost 34% of its charts that way; Companella lost 92 to the
        fast end).  When a target escapes, probe the next rungs of a short ladder
        (0.8 then 0.7 downwards, 3.0 then 4.0 upwards), fold the reading into the
        curve and re-invert; anything still outside is clamped to the ladder end
        so the chart keeps a reading instead of vanishing.

        The probes are *not* merged into ``rate_curves``: AIN/CT filter to
        rate >= 1.0 and a 3.0/4.0 point would silently change them.
        """
        low_ladder = (0.8, 0.7)
        high_ladder = (3.0, 4.0)

        resolved: Dict[str, float] = {}
        pending: Dict[str, float] = {}
        for name, target in targets.items():
            curve = curves.get(name)
            if not curve:
                continue
            star = _invert_rate_curve(curve, target)
            if star is not None:
                resolved[name] = star
            else:
                pending[name] = target

        probed = 0
        for ladder in (low_ladder, high_ladder):
            if not pending:
                break
            lo_side = {
                n: t
                for n, t in pending.items()
                if t < min(d for _, d in curves[n])
            }
            group = lo_side if ladder is low_ladder else {
                n: t for n, t in pending.items() if n not in lo_side
            }
            for rung in ladder:
                if not group:
                    break
                tasks = [
                    (n, [float(rung)], contents[n])
                    for n in group
                    if n in contents
                ]
                if not tasks:
                    break
                out = self._map(pool, run_calc_rate_batch, tasks, chunksize=1)
                got: Dict[str, float] = {}
                for chunk in out:
                    for name, _rate, diff in chunk:
                        if diff is not None:
                            got[name] = diff
                probed += len(got)
                still: Dict[str, float] = {}
                for name, target in group.items():
                    if name not in got:
                        still[name] = target
                        continue
                    extended = sorted(list(curves[name]) + [(float(rung), got[name])])
                    star = _invert_rate_curve(extended, target)
                    if star is not None:
                        resolved[name] = star
                        pending.pop(name, None)
                    else:
                        still[name] = target
                group = still

        # Still unbracketed: clamp to the ladder end so the sample survives.
        for name, _target in list(pending.items()):
            curve = curves.get(name)
            if not curve:
                continue
            target = pending[name]
            resolved[name] = (
                low_ladder[-1] if target < min(d for _, d in curve) else high_ladder[-1]
            )
            pending.pop(name, None)

        if probed:
            warnings.warn(
                f"{label}: {probed} probe reading(s) below rate_floor / above "
                f"rate_max were evaluated on demand to keep those charts in the "
                "median (previously they were dropped)",
                RuntimeWarning,
                stacklevel=3,
            )
        return resolved

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
        arrangement_sensitivity_pct: Optional[float] = None,
        ho_delta_pct: Optional[float] = None,
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
            overridden_sp = _load_speed_result(self.config.sp_source, self.adapter.name)
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

        # r* ("which clock rate would reproduce this reading") is derived from
        # the per-map rate curve, which is algorithm-level and already computed.
        # The sub-tier path used to skip it entirely and fall back to the legacy
        # ITR/MAPE curves, so the same algorithm was measured with two different
        # rulers depending on which table you read (CODE_REVIEW P1-01).
        tier_rate_lookup: Dict[str, List[Tuple[float, float]]] = {
            s.name: rate_curves[s.name] for s in tier_pert_samples if s.name in rate_curves
        }

        def _tier_rstar(ledger: Dict[str, Tuple[float, float]], idx: int) -> Tuple[Optional[float], float]:
            vals: List[float] = []
            for _name, pair in ledger.items():
                if _name not in tier_rate_lookup:
                    continue
                curve = tier_rate_lookup[_name]
                if not curve:
                    continue
                star = _invert_rate_curve(curve, pair[idx])
                if star is not None:
                    vals.append(star)
            if not vals:
                return None, 0.0
            return float(np.median(vals)), len(vals) / max(1, len(ledger))

        tier_jitter_rstar, tier_jitter_cov = _tier_rstar(jitter_map, 1)
        tier_sln_rstar, tier_sln_cov = _tier_rstar(sln_map, 1)

        tier_jitter_orig = [jitter_map[s.name][0] for s in tier_pert_samples if s.name in jitter_map]
        tier_jitter_diff = [jitter_map[s.name][1] for s in tier_pert_samples if s.name in jitter_map]
        tier_jitter_res = evaluate_jitter(
            tier_jitter_orig,
            tier_jitter_diff,
            arrangement_sensitivity_pct=arrangement_sensitivity_pct,
            rstar_median=tier_jitter_rstar,
            rstar_coverage=tier_jitter_cov,
            num_expected_maps=len(tier_pert_samples),
            dropped_maps=[s.name for s in tier_pert_samples if s.name not in jitter_map],
        )

        tier_sln_orig = [sln_map[s.name][0] for s in tier_pert_samples if s.name in sln_map]
        tier_sln_diff = [sln_map[s.name][1] for s in tier_pert_samples if s.name in sln_map]
        tier_sln_res = evaluate_short_ln(
            tier_sln_orig,
            tier_sln_diff,
            is_brute_force_tap=is_brute_force_tap,
            ln_excluded=ln_excluded,
            ho_delta_pct=ho_delta_pct,
            rstar_median=tier_sln_rstar,
            rstar_coverage=tier_sln_cov,
            num_expected_maps=len(tier_pert_samples),
            dropped_maps=[s.name for s in tier_pert_samples if s.name not in sln_map],
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
            samples=list(tier_acc_samples),
            raw_predictions=list(tier_raw_preds),
            jitter_ledger=dict(jitter_map),
            sln_ledger=dict(sln_map),
        )

    def run_all(self, verbose: bool = True) -> BenchmarkRunResult | MultiTierBenchmarkResult:
        """Run all 6 benchmark dimensions across single or multi-tier configurations."""
        t_start = time.time()
        # --sp-source is an explicit promise: fail before spending an hour of
        # compute rather than silently scoring this algorithm's own speed.
        # (It used to warn and continue, which turned a typo into a plausible
        # but wrong Speed dimension -- e.g. a 40x-faster port inheriting nothing
        # and jumping from 61 to 77 points.)
        if self.config.sp_source and _load_speed_result(
            self.config.sp_source, self.adapter.name
        ) is None:
            raise RuntimeError(
                f"--sp-source {self.config.sp_source!r} could not be read: missing "
                "file, a different algorithm's archive, or one with no evaluated "
                "samples. SP would otherwise silently fall back to this "
                "algorithm's own speed."
            )
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
            # Fail fast if the initializer raised (missing Daniel checkout,
            # unimportable adapter): multiprocessing would otherwise respawn
            # workers forever and the first map() would never return (P1-17).
            try:
                healthy = self._map(pool, run_ping, [None], timeout=60.0)
            except RuntimeError as exc:
                raise RuntimeError(
                    f"worker initialization failed ({adapter_type}): {exc}. "
                    "Check the adapter path / dependencies."
                ) from None
            if not healthy or not healthy[0]:
                raise RuntimeError(
                    f"worker initialization failed ({adapter_type}): adapter not constructed"
                )

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
            calc_outputs = self._map(pool, run_calc_path_timed, file_paths)

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

            # Hard gate: a run in which nothing succeeded used to emit a
            # perfectly valid-looking 32.3/100 report and exit 0, because an
            # empty accuracy pool scored 40 (zero RMSE) and the empty rate /
            # continuity pools scored 100 ("no inversion", "no cliffs").
            # Publishing that number is worse than crashing (P0-01/P0-08).
            if not acc_samples:
                total_attempted = len(file_paths)
                raise RuntimeError(
                    f"no beatmap could be evaluated ({total_attempted} attempted, "
                    f"{len(failed_beatmaps)} failed). Refusing to write a report. "
                    f"First errors: "
                    f"{'; '.join(fb['name'] + ': ' + fb['error'] for fb in failed_beatmaps[:3])}"
                )
            failure_ratio = len(failed_beatmaps) / max(1, len(file_paths))
            if failure_ratio > 0.01:
                import warnings

                warnings.warn(
                    f"{len(failed_beatmaps)}/{len(file_paths)} beatmaps failed "
                    f"({failure_ratio:.1%}); scores are computed on the survivors "
                    "and are NOT comparable with a full run",
                    RuntimeWarning,
                    stacklevel=2,
                )

            acc_timings = [
                (calc_times_ms[i], s.duration_seconds)
                for i, s in enumerate(acc_samples)
            ]

            acc_result = evaluate_accuracy(
                samples=acc_samples,
                raw_predictions=raw_predictions,
                scale_type=self.adapter.scale_type,
            )

            # Declared difficulty scope: a *secondary* Accuracy reading over the
            # kept beatmaps only, with its own anchors. This never alters the
            # headline metrics, which always span the full dataset.
            scope_report, in_scope_accuracy, in_scope_predictions = self._evaluate_scope_accuracy(
                acc_samples=acc_samples,
                raw_predictions=raw_predictions,
            )

            if verbose:
                print(
                    f"  -> Calibrated ND-RMSE: {acc_result.global_rmse:.4f} | "
                    f"Spearman rho: {acc_result.spearman_rho:.4f} | "
                    f"WTD: {acc_result.mean_wtd:.4f}"
                )
                if scope_report is not None and scope_report.declarations:
                    if in_scope_accuracy is not None:
                        from cab.metrics.scoring import score_accuracy

                        in_score, in_sev, in_pen = score_accuracy(in_scope_accuracy)
                        print(
                            f"  -> In-scope ({scope_report.description}): "
                            f"RMSE {in_scope_accuracy.global_rmse:.4f} | "
                            f"rho {in_scope_accuracy.spearman_rho:.4f} | "
                            f"score {in_score:.2f}/100 "
                            f"(in-scope inversion S {in_sev:.4f} -> -{in_pen:.2f}) | "
                            f"{len(scope_report.in_scope_samples)}/{len(acc_samples)} charts | "
                            f"gives up {scope_report.out_of_scope_usage * 100:.1f}% of real-world usage"
                        )
                    else:
                        print("  [!] Declared scope left no in-scope charts; secondary ACC skipped.")
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

            # Pre-read beatmap contents for perturbation samples.  An I/O error
            # here used to abort the whole run *after* the accuracy stage had
            # already cost hours, with no partial result saved (CODE_REVIEW
            # P2-14).  A single unreadable chart is now skipped instead.
            beatmap_contents: Dict[str, str] = {}
            for s in pert_samples:
                try:
                    with open(s.file_path, "r", encoding="utf-8", errors="replace") as fp:
                        beatmap_contents[s.name] = fp.read()
                except OSError as exc:
                    warnings.warn(
                        f"cannot read {s.file_path} for perturbation: {exc}; "
                        "chart skipped in the dynamic dimensions",
                        RuntimeWarning,
                        stacklevel=2,
                    )
            pert_samples = [s for s in pert_samples if s.name in beatmap_contents]

            rate_curves: Dict[str, List[Tuple[float, float]]] = {}

            if self.config.adaptive_rate:
                # ---------------------------------------------------------
                # Adaptive two-phase rate sweep:
                #   Phase 1: coarse scan on a tidy decade ladder
                #            (1.0, 1.1, 1.2, ... 2.0)
                #   Phase 2: fine refinement only on suspicious maps/intervals
                # ---------------------------------------------------------
                # 1.0x is skipped here: the accuracy stage already produced that
                # exact reading, so it is re-injected from base_diff_map below
                # instead of paying for an identical evaluation.
                coarse_rates = [
                    r
                    for r in _build_rate_grid(
                        self.config.rate_min,
                        self.config.rate_max,
                        0.1,
                        getattr(self.config, "rate_floor", None),
                    )
                    if abs(r - 1.0) > 1e-9
                ]
                # One task per chart: the chart is parsed once inside the worker
                # and re-scaled for every rate (see run_calc_rate_batch).  The
                # sweep used to build one task per (chart, rate), which paid the
                # 3.9 ms parse 12,140 times instead of 600.
                coarse_tasks = [
                    (s.name, [float(r) for r in coarse_rates], beatmap_contents[s.name])
                    for s in pert_samples
                ]
                if verbose:
                    print(
                        f"  -> [Adaptive] Phase 1 coarse scan: {len(pert_samples)} maps x "
                        f"{len(coarse_rates)} rates "
                        f"({len(pert_samples) * len(coarse_rates)} evaluations)..."
                    )
                coarse_outputs = self._map(
                    pool,
                    run_calc_rate_batch,
                    coarse_tasks,
                    chunksize=1,
                )
                for chunk in coarse_outputs:
                    for map_name, r, diff in chunk:
                        if diff is not None:
                            rate_curves.setdefault(map_name, []).append((r, diff))
                for _s in pert_samples:
                    _base = base_diff_map.get(_s.name)
                    if _base is not None:
                        rate_curves.setdefault(_s.name, []).append((1.0, float(_base)))
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
                            # Sub-unity refinement is wasted work: AIN and CT both
                            # evaluate with min_rate=1.0 and drop those points, so
                            # the only consumer is r* -- which now probes on demand
                            # (see _resolve_rstar) instead of paying for a dense
                            # grid nobody reads.  The single coarse 0.9 anchor from
                            # phase 1 stays, because targets in [D(0.9), D(1.0)]
                            # still interpolate against it.
                            if float(r) >= self.config.rate_min - 1e-9:
                                new_rates.add(round(float(r), 3))
                            r += fine_step
                    if new_rates:
                        refine_tasks.append(
                            (s.name, sorted(new_rates), beatmap_contents[s.name])
                        )

                if refine_tasks:
                    _n_refine = sum(len(t[1]) for t in refine_tasks)
                    if verbose:
                        print(
                            f"  -> [Adaptive] Phase 2 refinement: {refined_maps} suspicious maps, "
                            f"{_n_refine} extra evaluations (step={fine_step})..."
                        )
                    refine_outputs = self._map(
                        pool,
                        run_calc_rate_batch,
                        refine_tasks,
                        chunksize=1,
                    )
                    for chunk in refine_outputs:
                        for map_name, r, diff in chunk:
                            if diff is not None:
                                rate_curves.setdefault(map_name, []).append((r, diff))
                    for map_name in rate_curves:
                        rate_curves[map_name].sort(key=lambda pt: pt[0])
            else:
                # Plain uniform rate sweep (legacy behavior / quick mode).
                # Uses the same grid builder, so quick mode also reaches 2.0.
                # 1.0x reused from the accuracy stage (see the adaptive branch).
                rates = [
                    r
                    for r in _build_rate_grid(
                        self.config.rate_min,
                        self.config.rate_max,
                        self.config.rate_step,
                        getattr(self.config, "rate_floor", None),
                    )
                    if abs(r - 1.0) > 1e-9
                ]

                rate_tasks = [
                    (s.name, [float(r) for r in rates], beatmap_contents[s.name])
                    for s in pert_samples
                ]

                if verbose:
                    print(f"  -> Evaluating {len(pert_samples)} maps across {len(rates)} rates ({len(pert_samples) * len(rates)} evaluations)...")

                rate_outputs = self._map(pool, run_calc_rate_batch, rate_tasks, chunksize=1)

                for chunk in rate_outputs:
                    for map_name, r, diff in chunk:
                        if diff is not None:
                            rate_curves.setdefault(map_name, []).append((r, diff))
                for _s in pert_samples:
                    _base = base_diff_map.get(_s.name)
                    if _base is not None:
                        rate_curves.setdefault(_s.name, []).append((1.0, float(_base)))

                for map_name in rate_curves:
                    rate_curves[map_name].sort(key=lambda pt: pt[0])

            # -------------------------------------------------------------
            # Phase 3: quantization-noise audit.
            #
            # ``scale_rate`` quantizes every timestamp to 1 ms, and that alone
            # can flip the sign of a sub-1% difficulty step: on Rip Me
            # Apart(theta) 1.26x -> 1.28x the same map reads -0.229% under
            # round() but +0.339% under floor().  Re-evaluate only the
            # intervals that *look* inverted, using the floor variant, and keep
            # half the round/floor spread as that point's noise floor.  The
            # cost scales with the number of suspicious steps, not the grid.
            # -------------------------------------------------------------
            noise_floors: Dict[str, Dict[float, float]] = {}
            audit_tasks: List[Tuple[str, float, str, str]] = []
            audit_keys: List[Tuple[str, float]] = []
            round_lookup: Dict[str, Dict[float, float]] = {}
            for map_name, curve in rate_curves.items():
                curve.sort(key=lambda pt: pt[0])
                round_lookup[map_name] = {float(r): float(d) for r, d in curve}
                for (r0, d0), (r1, d1) in zip(curve, curve[1:]):
                    if d1 >= d0 - 1e-12:
                        continue
                    for r in (r0, r1):
                        audit_tasks.append(
                            (map_name, float(r), beatmap_contents[map_name], "floor")
                        )
                        audit_keys.append((map_name, float(r)))

            if audit_tasks:
                audit_outputs = self._map(pool, 
                    run_calc_rate_job,
                    audit_tasks,
                    chunksize=max(1, len(audit_tasks) // (self.config.workers * 4)),
                )
                for (map_name, r), (_n, _r, d_floor) in zip(audit_keys, audit_outputs):
                    if d_floor is None:
                        continue
                    d_round = round_lookup.get(map_name, {}).get(r)
                    if d_round is None:
                        continue
                    noise_floors.setdefault(map_name, {})[r] = abs(
                        d_round - float(d_floor)
                    ) / 2.0
                if verbose:
                    _audited = sum(len(v) for v in noise_floors.values())
                    print(
                        f"  -> [Audit] 1 ms quantization noise: {_audited} rate points "
                        f"re-evaluated with floor() on {len(noise_floors)} maps"
                    )

            inv_result = evaluate_rate_inversion(rate_curves, noise_floors=noise_floors or None)
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

            jitter_outputs = self._map(pool, run_calc_jitter_job, jitter_tasks)
            jitter_orig_diffs = []
            jitter_diffs = []
            jitter_map: Dict[str, Tuple[float, float]] = {}
            jitter_dropped: List[str] = []
            for (s, orig_d), (_, j_diff) in zip(jitter_sample_pairs, jitter_outputs):
                if j_diff is not None:
                    jitter_orig_diffs.append(orig_d)
                    jitter_diffs.append(j_diff)
                    jitter_map[s.name] = (orig_d, j_diff)
                else:
                    # Record instead of silently dropping: a smaller N used to
                    # look like a normal run, and a slow adapter whose server
                    # kept dying could quietly be scored on its easiest charts.
                    jitter_dropped.append(s.name)

            # Intra-window arrangement sensitivity probe (anti-density-blindness gate)
            arrangement_sensitivity_pct: Optional[float] = None
            if jitter_sample_pairs:
                step = max(1, len(jitter_sample_pairs) // 15)
                arr_probe_pairs = jitter_sample_pairs[::step][:15]
                arr_tasks = [(s.name, beatmap_contents[s.name]) for s, _ in arr_probe_pairs]
                arr_outputs = self._map(pool, run_calc_arrangement_probe_job, arr_tasks)
                arr_orig_vals = []
                arr_mut_vals = []
                for (s, orig_d), (_, a_diff) in zip(arr_probe_pairs, arr_outputs):
                    if a_diff is not None:
                        arr_orig_vals.append(float(orig_d))
                        arr_mut_vals.append(float(a_diff))
                if arr_orig_vals:
                    arr_orig_arr = np.array(arr_orig_vals, dtype=float)
                    arr_mut_arr = np.array(arr_mut_vals, dtype=float)
                    arr_d_min = float(min(0.0, np.min(arr_orig_arr)))
                    arr_denom = np.maximum(arr_orig_arr - arr_d_min, 1.0)
                    arrangement_sensitivity_pct = float(
                        np.mean(np.abs(arr_mut_arr - arr_orig_arr) / arr_denom) * 100.0
                    )

            # Express each jitter delta as an equivalent clock rate.  r* == 1
            # means the ~10 ms nudge changed nothing, which is the physically
            # correct response; both directions of departure are over-reactions,
            # and a drop is penalised harder than a rise.
            _jit_targets = {n: pair[1] for n, pair in jitter_map.items()}
            _jit_rstar = self._resolve_rstar(
                pool, rate_curves, _jit_targets, beatmap_contents, label="Anti-Jitter"
            )
            rstar_vals: List[float] = list(_jit_rstar.values())
            rstar_median = float(np.median(rstar_vals)) if rstar_vals else None
            rstar_coverage = (len(rstar_vals) / len(jitter_map)) if jitter_map else 0.0

            jitter_result = evaluate_jitter(
                jitter_orig_diffs,
                jitter_diffs,
                arrangement_sensitivity_pct=arrangement_sensitivity_pct,
                rstar_median=rstar_median,
                rstar_coverage=rstar_coverage,
                num_expected_maps=len(jitter_sample_pairs),
                dropped_maps=jitter_dropped,
            )

            if verbose:
                arr_str = (
                    f" | Arrangement Probe: {arrangement_sensitivity_pct:.2f}%"
                    if arrangement_sensitivity_pct is not None
                    else ""
                )
                print(
                    f"  -> Jitter Self-RMSE: {jitter_result.jitter_rmse:.5f} | "
                    f"MAPE: {jitter_result.mape:.3f}% | "
                    f"Max Delta: {jitter_result.max_absolute_delta:.5f}{arr_str} | "
                    f"Score: {jitter_result.score:.2f}/100"
                )
                if jitter_result.is_arrangement_blind:
                    print(
                        f"  [!] Window-Density Arrangement Blindness Detected: "
                        f"Arrangement Probe delta = {arrangement_sensitivity_pct:.2f}% (< 3.0%)"
                    )
            _check_probe_coverage(
                "Anti-Jitter",
                len(jitter_diffs),
                len(jitter_sample_pairs),
                jitter_dropped,
                verbose=verbose,
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

            sln_outputs = self._map(pool, run_calc_short_ln_job, short_ln_tasks)
            sln_orig_diffs = []
            sln_diffs = []
            sln_map: Dict[str, Tuple[float, float]] = {}
            sln_dropped: List[str] = []
            for (s, orig_d), (_, s_diff) in zip(sln_sample_pairs, sln_outputs):
                if s_diff is not None:
                    sln_orig_diffs.append(orig_d)
                    sln_diffs.append(s_diff)
                    sln_map[s.name] = (orig_d, s_diff)
                else:
                    sln_dropped.append(s.name)

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
            mean_ho_delta_pct: Optional[float] = None
            if not ln_excluded:
                ln_samples = [s for s in self.dataset.samples if s.is_ln]
                if ln_samples:
                    probe_samples = ln_samples[:5]
                    ho_tasks = []
                    for s in probe_samples:
                        bm = Beatmap.parse(s.file_path)
                        bm_ho = apply_hold_off(bm)
                        ho_tasks.append((str(s.file_path), bm_ho.serialize()))

                    ho_outputs = self._map(pool, run_calc_ho_probe_job, ho_tasks)
                    valid_ho = [
                        (float(d_orig), float(d_ho))
                        for d_orig, d_ho in ho_outputs
                        if d_orig is not None and d_ho is not None
                    ]
                    if valid_ho:
                        ho_orig_arr = np.array([p[0] for p in valid_ho], dtype=float)
                        ho_mut_arr = np.array([p[1] for p in valid_ho], dtype=float)
                        ho_d_min = float(min(0.0, np.min(ho_orig_arr)))
                        ho_denom = np.maximum(ho_orig_arr - ho_d_min, 1.0)
                        mean_ho_delta_pct = float(
                            np.mean(np.abs(ho_orig_arr - ho_mut_arr) / ho_denom) * 100.0
                        )
                        if mean_ho_delta_pct < 2.0:
                            is_brute_force_tap = True
                            if verbose:
                                print(f"  [!] Brute-Force Tap Stripping Detected: LN vs HO delta = {mean_ho_delta_pct:.2f}% (< 2.0%)")

            # Express the short-LN delta as an equivalent clock rate, exactly
            # as the jitter dimension does.  r* = 1 means "shortening the LNs
            # changed nothing", which is the target: a 49-59 ms LN needs no
            # change in hold habit, and the release judgement is more forgiving
            # than a tap while still earning its own accuracy weight.
            sln_rstar_vals: List[float] = []
            _sln_targets = {n: pair[1] for n, pair in sln_map.items()}
            _sln_rstar = self._resolve_rstar(
                pool,
                rate_curves,
                _sln_targets,
                beatmap_contents,
                label="Anti-Short-LN",
            )
            sln_rstar_vals = list(_sln_rstar.values())
            sln_rstar_median = float(np.median(sln_rstar_vals)) if sln_rstar_vals else None
            sln_rstar_coverage = (
                len(sln_rstar_vals) / len(sln_map) if sln_map else 0.0
            )

            sln_result = evaluate_short_ln(
                sln_orig_diffs,
                sln_diffs,
                is_brute_force_tap=is_brute_force_tap,
                ln_excluded=ln_excluded,
                ho_delta_pct=mean_ho_delta_pct,
                rstar_median=sln_rstar_median,
                rstar_coverage=sln_rstar_coverage,
                num_expected_maps=len(sln_sample_pairs),
                dropped_maps=sln_dropped,
            )
            _check_probe_coverage(
                "Anti-Short-LN",
                len(sln_diffs),
                len(sln_sample_pairs),
                sln_dropped,
                verbose=verbose,
            )

            if verbose:
                if ln_excluded:
                    print(f"  [!] LN dataset excluded by tester / preset: Anti-Short LN has nothing to measure -> 0.0 pts")
                elif is_brute_force_tap:
                    print(
                        f"  [!] Algorithm strips LN to tap (LN-blind): gated by continuous HO capability probe "
                        f"(HO delta = {mean_ho_delta_pct or 0.0:.2f}%)"
                    )
                print(
                    f"  -> Short-LN Self-RMSE: {sln_result.short_ln_rmse:.5f} | "
                    f"Inflation (ITR): {sln_result.inflation_tendency_ratio:+.3f}% | "
                    f"Grade: {sln_result.immunity_grade} | Score: {sln_result.score:.2f}/100"
                )

        # -------------------------------------------------------------
        # Dimension 6: Algorithmic Computation Speed Benchmark (ms/s)
        # -------------------------------------------------------------
        # Measured on a dedicated *serial* probe instead of reusing the accuracy
        # timings: with N workers in flight the per-call time is inflated by
        # CPU / memory-bandwidth contention (measured on Mixed: 0.7606 ms/s at
        # 4 workers vs 0.9562 ms/s at 8), which made SP depend on the worker
        # count.  Pinning the sample set to the 33 dan-course marathons also
        # removes short-chart timing noise from the ratio.
        speed_probe_samples = (
            [s for s in self.dataset.get_accuracy_samples() if _is_speed_probe(s)]
            if getattr(self.config, "speed_probe", True)
            else []
        )
        if speed_probe_samples:
            if verbose:
                _probe_minutes = sum(
                    s.load_beatmap().duration_seconds for s in speed_probe_samples
                ) / 60.0
                print(
                    f"  -> Serial speed probe: {len(speed_probe_samples)} dan-course "
                    f"marathons, {_probe_minutes:.0f} min of chart (warm-up discarded)"
                )
            with ctx.Pool(
                processes=1,
                initializer=init_worker,
                initargs=(adapter_type, adapter_kwargs),
            ) as sp_pool:
                sp_paths = [str(s.file_path) for s in speed_probe_samples]
                self._map(sp_pool, run_calc_path_timed, sp_paths[:1])  # warm-up, discarded
                # Query the baseline from inside the same worker, after warm-up,
                # so it comes from the same persistent server the samples below
                # will go through.
                _ovh_raw = self._map(sp_pool, run_measure_overhead, [None])
                sp_outputs = self._map(sp_pool, run_calc_path_timed, sp_paths)
            sp_timings = []
            for _s, (_diff, _dt, _err) in zip(speed_probe_samples, sp_outputs):
                if _diff is None:
                    continue
                sp_timings.append((_dt, _s.load_beatmap().duration_seconds))

            # ── Cross-machine SP baseline (MinaCalc) ───────────────────────
            # Time the same 33-chart probe through the built-in MinaCalc
            # adapter moments later on the same machine, so rho = v_algo /
            # v_baseline cancels both the machine's speed and the current load
            # state.  Skipped when the algorithm under test IS MinaCalc (its
            # own timing is the baseline -> rho = 1 -> 90 points).
            _base_median: Optional[float] = None
            _is_base_algo = (self.adapter.name or "").lower() in (
                "minacalc",
                "minacalc-0.72",
                "minacalc-0.72.3",
                "minacalc 515",
            )
            if _is_base_algo:
                if sp_timings:
                    _base_median = evaluate_speed(sp_timings).median_speed_ms_per_s
            else:
                try:
                    _runner = (
                        Path(__file__).resolve().parents[2]
                        / "cab/adapter/node_runners/estimator_cli.mjs"
                    )
                    _base_kwargs = {
                        "name": "MinaCalc-0.72.3 (SP baseline)",
                        "cmd_template": f"node {_runner} --algo minacalc --file {{input}}",
                        "scale_type": "non-nd",
                        "timeout_seconds": 300.0,
                        "server_cmd": f"node {_runner} --algo minacalc --server",
                        "overhead_probe_cmd": None,
                    }
                    with ctx.Pool(
                        processes=1,
                        initializer=init_worker,
                        initargs=("cli", _base_kwargs),
                    ) as _base_pool:
                        _base_paths = [str(s.file_path) for s in speed_probe_samples]
                        self._map(_base_pool, run_calc_path_timed, _base_paths[:1])  # warm-up
                        _base_outputs = self._map(_base_pool, run_calc_path_timed, _base_paths)
                    _base_timings = []
                    for _s, (_bd, _bdt, _berr) in zip(speed_probe_samples, _base_outputs):
                        if _bd is None:
                            continue
                        _base_timings.append(
                            (_bdt, _s.load_beatmap().duration_seconds)
                        )
                    if _base_timings:
                        _base_median = evaluate_speed(
                            _base_timings
                        ).median_speed_ms_per_s
                        if verbose:
                            print(
                                f"  -> SP baseline (MinaCalc): "
                                f"{_base_median:.4f} ms/s median"
                            )
                except Exception as _bexc:
                    if verbose:
                        print(
                            f"  -> [warn] SP baseline unavailable ({_bexc}); "
                            f"falling back to the absolute speed curve"
                        )
            # Compatibility-layer overhead (Wine for Dan-Overlay's msd.exe) is
            # a fixed per-invocation cost that the target platform does not pay.
            # Subtract it so the dimension measures the algorithm rather than
            # "how fast Wine starts".  Adapters running natively (or without a
            # probe) report 0.0 and are unaffected.
            _ovh_ms = float(_ovh_raw[0] or 0.0) if _ovh_raw else 0.0
            if verbose and _ovh_ms > 0.0:
                print(
                    f"  -> Compatibility-layer overhead: {_ovh_ms:.1f} ms per "
                    f"invocation subtracted (Wine start-up, not algorithm cost)"
                )
            speed_result = evaluate_speed(
                sp_timings,
                platform_overhead_ms=_ovh_ms,
                baseline_ms_per_s=_base_median,
                baseline_algorithm="MinaCalc-0.72.3" if _base_median else "",
            )
            # Coverage gate: a slow algorithm whose slowest marathons time out
            # would otherwise be measured only on the charts it managed to
            # finish, i.e. it would look *faster* the slower it is.  Never fall
            # back to acc_timings either: those were taken with `workers`
            # processes competing, which is exactly the bias this probe exists
            # to remove (CODE_REVIEW P1-15).
            if sp_timings and len(sp_timings) < 0.9 * len(speed_probe_samples):
                import warnings

                warnings.warn(
                    f"SP probe covered only {len(sp_timings)}/{len(speed_probe_samples)} "
                    f"charts; the speed dimension is degraded (slow charts were dropped)",
                    RuntimeWarning,
                    stacklevel=2,
                )
                if len(sp_timings) < 0.5 * len(speed_probe_samples):
                    speed_result = evaluate_speed([])
        else:
            speed_result = evaluate_speed(acc_timings)
        if verbose:
            print("\n[6/6] Computing Algorithmic Calculation Speed (ms/s)...")
            _rf = f"{speed_result.realtime_factor:.1f}x" if speed_result.realtime_factor is not None else "N/A"
            _score = f"{speed_result.speed_score:.1f}/100" if speed_result.speed_score is not None else "N/A (not evaluated)"
            print(
                f"  -> Average Speed: {speed_result.average_speed_ms_per_s:.3f} ms/s | "
                f"Median: {speed_result.median_speed_ms_per_s:.3f} ms/s | "
                f"Realtime Factor: {_rf} | "
                f"Score: {_score}"
            )

        # SP provenance override: fast drop-in replacements (e.g. sunny-rs)
        # inherit the SP dimension from the original algorithm's run so the
        # score keeps reflecting the original's real speed.
        if self.config.sp_source:
            overridden = _load_speed_result(self.config.sp_source, self.adapter.name)
            if overridden is not None:
                speed_result = overridden
                if verbose:
                    print(
                        f"  [i] SP inherited from '{self.config.sp_source}' "
                        f"(original algorithm): {speed_result.speed_score:.1f}/100 "
                        f"@ {speed_result.median_speed_ms_per_s:.3f} ms/s median"
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
            samples=list(acc_samples),
            raw_predictions=list(raw_predictions),
            jitter_ledger=dict(jitter_map),
            sln_ledger=dict(sln_map),
            rstar_jitter=dict(_jit_rstar),
            rstar_sln=dict(_sln_rstar),
            rate_noise_floors=dict(noise_floors),
            scope_report=scope_report,
            in_scope_accuracy=in_scope_accuracy,
            in_scope_predictions=in_scope_predictions,
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
                    arrangement_sensitivity_pct=arrangement_sensitivity_pct,
                    ho_delta_pct=mean_ho_delta_pct,
                )
                tier_results[t_id] = tier_res

            if verbose:
                print(f"\n============================================================")
                print(f"BENCHMARK COMPLETE (Elapsed: {elapsed:.2f}s)")
                print(f"Total Standardized CAB-Score (All Range): {score_breakdown.total_score:.1f} / 100")
                print(f"============================================================")
                for dim, pts in score_breakdown.dimension_scores.items():
                    beta = score_breakdown.dimension_betas.get(dim)
                    disc = score_breakdown.dimension_discounts.get(dim)
                    if dim == "Ground-Truth Accuracy":
                        note = "backbone"
                    elif beta is None:
                        note = "n/a"
                    elif disc is None:
                        note = f"beta {beta:.2f} (not evaluated)"
                    else:
                        note = f"beta {beta:.2f} -> x{disc:.3f}"
                    if pts is None:
                        print(f"  * {dim:28s}: {'N/A':>5}             ({note})")
                    else:
                        print(f"  * {dim:28s}: {pts:5.1f} / 100 pts ({note})")

                print("\n" + "=" * 105)
                print("CAB-VA MULTI-TIER BENCHMARK SUMMARY (4 TIERS)")
                print(f"Target Algorithm: {self.adapter.name} | Scale: {self.adapter.scale_type} | Mode: {self.config.mode.upper()}")
                print("=" * 105)
                print(f"{'Tier Name':<20} {'Range':<15} {'Maps':<6} {'CAB-Score':<11} {'ACC(base)':<13} {'AIN(b.15)':<10} {'ASLN(b.15)':<14} {'CT(b.10)':<9} {'SPD(b.05)':<12} {'AJ(b.05)':<12}")
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
                        spd_str = (
                            f"{tr.score.s_speed:5.1f}"
                            if tr.score.s_speed is not None
                            else "-"
                        )
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
                print("* Note: Total = ACC x PROD[1 - beta_i*(1 - s_i/100)]. ACC is the backbone; beta = max discount")
                print("        when a dimension scores 0. Sub-tiers omit AIN / CT / SP from the product\n"
                      "        (those are evaluated in All Range only).\n")

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
            arrangement_sensitivity_pct=arrangement_sensitivity_pct,
            ho_delta_pct=mean_ho_delta_pct,
        )
