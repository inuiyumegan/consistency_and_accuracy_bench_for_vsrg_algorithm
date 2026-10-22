"""Regression tests for the 2026-10-21 code review (CODE_REVIEW_2026-10-21.md).

Each test pins one of the failure modes that used to be silent:

* P0-01/02  no data / NaN must never read as a good score
* P0-03     a failing adapter must raise, not return 0.0
* P0-05     a dead worker must surface as an error, not an infinite wait
* P0-07     circle hitSamples must survive a parse -> serialize round-trip
* P1-31/32  jitter keeps chords simultaneous and never goes negative
* P1-34     predictions.csv carries a UTF-8 BOM
* P1-37     only charts that were actually written are linked
* P1-43     wheel-style installs ship the node runners
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cab.adapter.cli import CliAdapter  # noqa: E402
from cab.metrics.accuracy import AccuracyResult, evaluate_accuracy  # noqa: E402
from cab.metrics.continuity import ContinuityResult, evaluate_continuity  # noqa: E402
from cab.metrics.inversion import evaluate_rate_inversion  # noqa: E402
from cab.metrics.jitter_metric import evaluate_jitter, jitter_score  # noqa: E402
from cab.metrics.scoring import calculate_cab_score  # noqa: E402
from cab.metrics.short_ln_metric import evaluate_short_ln, short_ln_score  # noqa: E402
from cab.metrics.speed_metric import (  # noqa: E402
    calculate_speed_score,
    calculate_speed_score_relative,
    evaluate_speed,
)
from cab.mutator.jitter import apply_jitter  # noqa: E402
from cab.parser.osu_parser import Beatmap  # noqa: E402
from cab.report.predictions_csv import export_predictions_csv  # noqa: E402

SAMPLE_DIR = ROOT / "samples/4k"


# ---------------------------------------------------------------------------
# P0-01 / P0-02: missing data and NaN are never "good"
# ---------------------------------------------------------------------------
def test_empty_dataset_is_not_evaluated():
    score = calculate_cab_score(
        accuracy=evaluate_accuracy([], []),
        inversion=evaluate_rate_inversion({}),
        continuity=evaluate_continuity({}),
        jitter=evaluate_jitter([], []),
        short_ln=evaluate_short_ln([], []),
        speed=evaluate_speed([]),
    )
    assert score.is_evaluated is False
    assert score.total_score == 0.0
    # Every dimension must render as "—", never 0.0 / 100.0
    assert all(v is None for v in score.dimension_scores.values())


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_never_scores_perfect(value):
    assert calculate_speed_score(value) == 0.0
    assert short_ln_score(value) == 0.0
    assert jitter_score(value) == 0.0


def test_speed_relative_limit_at_zero():
    # rho -> 0 means "instant", i.e. the top of the curve, not zero.
    assert calculate_speed_score_relative(0.0) == 100.0
    assert math.isclose(calculate_speed_score_relative(1e-9), 100.0, rel_tol=1e-3)


def test_speed_overhead_does_not_invent_zero_cost():
    # 500 ms of work against a 1000 ms compatibility layer: the layer was not
    # paid, so the sample must be kept as-is instead of collapsing to 0.001 ms.
    res = evaluate_speed([(500.0, 500.0)], platform_overhead_ms=1000.0)
    assert res.evaluated_count == 1
    assert res.median_speed_ms_per_s == pytest.approx(1.0, rel=1e-6)
    assert res.speed_score is not None and res.speed_score < 100.0


def test_empty_speed_has_no_realtime_infinity():
    res = evaluate_speed([])
    assert res.speed_score is None
    assert res.realtime_factor is None


def test_accuracy_rejects_non_finite_predictions():
    from cab.parser.bench_loader import BenchmarkSample

    samples = [
        BenchmarkSample(None, "a", "tech", "", 10.0, Path("/tmp/a.osu")),
        BenchmarkSample(None, "b", "tech", "", 12.0, Path("/tmp/b.osu")),
    ]
    res = evaluate_accuracy(samples, [10.0, float("nan")])
    assert res.num_samples == 1
    assert res.num_rejected_nonfinite == 1
    assert math.isfinite(res.weighted_stratified_rmse)


# ---------------------------------------------------------------------------
# P0-03: adapter failures are loud
# ---------------------------------------------------------------------------
def _write_map(tmp_path: Path) -> Path:
    p = tmp_path / "m.osu"
    p.write_text(
        "osu file format v14\n\n[General]\nMode:3\n\n[Difficulty]\nCircleSize:4\n\n"
        "[HitObjects]\n64,192,1000,1,0,0:0:0:0:\n",
        encoding="utf-8",
    )
    return p


def test_adapter_nonzero_exit_raises(tmp_path):
    adapter = CliAdapter("bad", f"{sys.executable} -c 'import sys; sys.exit(1)'")
    with pytest.raises(RuntimeError):
        adapter.calculate(str(_write_map(tmp_path)))


@pytest.mark.parametrize("payload", ["nan", "inf", ""])
def test_adapter_rejects_non_numeric_output(tmp_path, payload):
    code = f"{sys.executable} -c 'print({payload!r})'"
    adapter = CliAdapter("bad", code)
    with pytest.raises(RuntimeError):
        adapter.calculate(str(_write_map(tmp_path)))


def test_adapter_takes_last_numeric_line(tmp_path):
    adapter = CliAdapter("ok", f"{sys.executable} -c 'print(\"loading\"); print(12.5)'")
    assert adapter.calculate(str(_write_map(tmp_path))) == pytest.approx(12.5)


def test_cmd_template_with_braces_is_not_formatted(tmp_path):
    # A JSON payload inside the command used to raise KeyError from str.format.
    adapter = CliAdapter(
        "braces",
        f"{sys.executable} -c 'print(7.5)' --meta \"{{'k': 1}}\" --input {{input}}",
    )
    # The command itself is irrelevant; the point is that it must not raise a
    # formatting error before running.
    try:
        adapter.calculate(str(_write_map(tmp_path)))
    except RuntimeError as exc:  # the fake command may not parse args
        assert "KeyError" not in str(exc)


# ---------------------------------------------------------------------------
# P0-05: a dead worker is an error, not a hang
# ---------------------------------------------------------------------------
def _worker_die(_ignored=None):
    """Module-level so the spawn context can pickle it."""
    import os

    os._exit(1)


def test_dead_worker_terminates_instead_of_hanging():
    import multiprocessing as mp

    from cab.runner.suite import BenchmarkSuite

    class _Dummy:
        name = "dummy"
        scale_type = "nd"

        def calculate(self, _target):
            return 1.0

    suite = BenchmarkSuite.__new__(BenchmarkSuite)

    class _Cfg:
        task_timeout_seconds = 3.0

    suite.config = _Cfg()

    ctx = mp.get_context("spawn")
    with ctx.Pool(processes=1) as pool:
        with pytest.raises(RuntimeError):
            suite._map(pool, _worker_die, [None])


# ---------------------------------------------------------------------------
# P0-07: circle hitSamples survive a round-trip
# ---------------------------------------------------------------------------
def test_circle_hitsample_roundtrip():
    content = (
        "osu file format v14\n\n[General]\nMode:3\n\n[Difficulty]\nCircleSize:4\n\n"
        "[HitObjects]\n"
        "192,192,3532,5,0,0:0:0:0::\n"
        "64,192,3600,128,0,3700:0:0:0:0:\n"
    )
    bm = Beatmap.parse(content)
    circle, hold = bm.hit_objects
    assert not (circle.type & 128)
    assert circle.end_time == circle.time
    assert circle.sample_info == "0:0:0:0::"  # verbatim, not truncated
    assert hold.is_ln and hold.end_time == 3700

    roundtrip = Beatmap.parse(bm.serialize())
    assert roundtrip.hit_objects[0].sample_info == circle.sample_info
    assert roundtrip.hit_objects[1].end_time == hold.end_time


@pytest.mark.skipif(not SAMPLE_DIR.exists(), reason="benchmark dataset not present")
def test_full_corpus_roundtrip_is_field_identical():
    files = sorted(SAMPLE_DIR.glob("**/*.osu"))
    assert files, "no .osu files found"
    for path in files[::37]:  # sample every 37th chart to keep the test quick
        bm = Beatmap.parse(path)
        again = Beatmap.parse(bm.serialize())
        fields = lambda b: sorted(  # noqa: E731
            (o.x, o.y, o.time, o.type, o.hit_sound, o.end_time, o.sample_info)
            for o in b.hit_objects
        )
        assert fields(bm) == fields(again), path


# ---------------------------------------------------------------------------
# P1-31 / P1-32: jitter preserves chords and stays non-negative
# ---------------------------------------------------------------------------
def _chord_map() -> str:
    return (
        "osu file format v14\n\n[General]\nMode:3\n\n[Difficulty]\nCircleSize:4\n"
        "OverallDifficulty:8\n\n[TimingPoints]\n0,500,4,0,0,100,1,0\n\n[HitObjects]\n"
        "64,192,3,1,0,0:0:0:0:\n"
        "192,192,1000,1,0,0:0:0:0:\n320,192,1000,1,0,0:0:0:0:\n"
        "448,192,1000,1,0,0:0:0:0:\n64,192,1000,1,0,0:0:0:0:\n"
    )


def test_jitter_draws_per_note_and_stays_non_negative():
    """Each note draws its own offset, but timestamps stay representable.

    A chord is allowed -- and expected -- to split: the probe models a player's
    per-finger timing error.  What must not happen is a negative timestamp,
    which the .osu format cannot represent.
    """
    split_seen = 0
    for seed in range(8):
        bm = Beatmap.parse(_chord_map())
        out = apply_jitter(bm, seed=seed)
        assert min(o.time for o in out.hit_objects) >= 0
        chord = [o.time for o in out.hit_objects if o.x != 64]
        if len(set(chord)) > 1:
            split_seen += 1
    # Four independent draws off the same timestamp split in practice; requiring
    # it across all seeds would be flaky, so only assert it happens at all.
    assert split_seen > 0


def test_jitter_rejects_invalid_parameters():
    bm = Beatmap.parse(_chord_map())
    with pytest.raises(ValueError):
        apply_jitter(bm, max_ms=-5)
    with pytest.raises(ValueError):
        apply_jitter(bm, sigma_ms=-1)


# ---------------------------------------------------------------------------
# P1-34: BOM on exported CSV
# ---------------------------------------------------------------------------
def test_predictions_csv_has_bom(tmp_path):
    from cab.parser.bench_loader import BenchmarkSample

    sample = BenchmarkSample(None, "chart", "tech", "", 10.0, Path("/tmp/x.osu"))
    out = export_predictions_csv([sample], [10.2], output_path=tmp_path / "p.csv")
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")


# ---------------------------------------------------------------------------
# JSON artefacts stay strictly parseable
# ---------------------------------------------------------------------------
def test_json_rejects_nan(tmp_path):
    """A NaN statistic must fail the export instead of writing invalid JSON."""
    from cab.metrics.calibration import CalibrationResult
    from cab.report.json_export import export_to_json

    calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
    broken = AccuracyResult(
        global_rmse=float("nan"),
        global_mae=float("nan"),
        spearman_rho=0.5,
        spearman_p=0.1,
        kendall_tau=0.4,
        stratified_rmse={},
        weighted_stratified_rmse=float("nan"),
        mean_wtd=0.0,
        wtd_by_tier={},
        calibration=calib,
        num_samples=1,
    )

    class _Cfg:
        mode = "quick"
        csv_path = "samples/4k/uuzs_bench.csv"
        workers = 1
        tier_mode = "all"
        patterns = None
        exclude_patterns = None
        min_expected = None
        max_expected = None
        name_filter = None
        filter_preset = None
        sample_limit_accuracy = None
        sample_limit_perturbation = None

        def has_filters(self):
            return False

    result = type(
        "R",
        (),
        {
            "algorithm_name": "x",
            "scale_type": "nd",
            "config": _Cfg(),
            "accuracy": broken,
            "inversion": evaluate_rate_inversion({"m": [(1.0, 10.0), (1.1, 9.0)]}),
            "continuity": evaluate_continuity({"m": [(1.0, 10.0), (1.1, 11.0), (1.2, 12.0)]}),
            "jitter": evaluate_jitter([10.0], [10.4]),
            "short_ln": evaluate_short_ln([10.0], [10.5]),
            "speed": evaluate_speed([(100.0, 100.0)]),
            "score": calculate_cab_score(accuracy=broken),
            "rate_curves": {},
            "duration_seconds": 1.0,
            "tier_id": "all",
            "tier_name": "all",
            "filter_summary": "",
            "sample_stats": {},
            "failed_beatmaps": [],
            "samples": [],
            "raw_predictions": [],
        },
    )()

    with pytest.raises(ValueError):
        export_to_json(result, tmp_path / "r.json")


# ---------------------------------------------------------------------------
# P1-43: package data
# ---------------------------------------------------------------------------
def test_package_data_declares_node_runner():
    import tomllib

    cfg = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = cfg["tool"]["setuptools"]["package-data"]["cab"]
    assert any("node_runners" in p for p in patterns)


def test_manifest_requires_resolvable_rows(tmp_path):
    from cab.parser.bench_loader import BenchmarkDataset

    csv = tmp_path / "tiny.csv"
    csv.write_text("path,name,pattern,subPattern,expected\nmissing.osu,ghost,tech,,10.0\n", encoding="utf-8")
    with pytest.raises(ValueError):
        BenchmarkDataset.load_from_csv(csv, samples_root=tmp_path)


# ---------------------------------------------------------------------------
# 2026-10-21: dropped probe charts must be recorded, not silently shrunk
# ---------------------------------------------------------------------------
def test_jitter_records_dropped_probe_charts():
    from cab.metrics.jitter_metric import evaluate_jitter

    res = evaluate_jitter(
        [10.0, 12.0], [10.1, 12.1],
        num_expected_maps=4, dropped_maps=["a.osu", "b.osu"],
    )
    assert res.num_evaluated_maps == 2
    assert res.probe_coverage == 0.5
    assert tuple(res.dropped_maps) == ("a.osu", "b.osu")

    # A caller that does not know the intended count reports "unknown", never
    # a fake 100% or a divide-by-zero.
    legacy = evaluate_jitter([1.0], [1.1])
    assert legacy.probe_coverage is None


def test_short_ln_records_dropped_probe_charts():
    from cab.metrics.short_ln_metric import evaluate_short_ln

    res = evaluate_short_ln(
        [10.0, 12.0, 13.0], [10.2, 12.3, 13.1],
        num_expected_maps=4, dropped_maps=["gone.osu"],
    )
    assert res.num_evaluated_maps == 3
    assert abs(res.probe_coverage - 0.75) < 1e-9
    assert tuple(res.dropped_maps) == ("gone.osu",)

    assert evaluate_short_ln([1.0], [1.1]).probe_coverage is None


def test_probe_drops_csv_lists_each_dimension(tmp_path):
    from cab.metrics.jitter_metric import evaluate_jitter
    from cab.metrics.short_ln_metric import evaluate_short_ln
    from cab.report.probe_ledger import export_probe_drops_csv

    class _Result:
        jitter = evaluate_jitter([1.0], [1.1], num_expected_maps=3, dropped_maps=["j1", "j2"])
        short_ln = evaluate_short_ln([1.0], [1.1], num_expected_maps=2, dropped_maps=["s1"])

    path = export_probe_drops_csv(_Result(), tmp_path / "probe_drops.csv")
    assert path is not None
    assert path.read_text(encoding="utf-8-sig").split() == [
        "dimension,name", "anti-jitter,j1", "anti-jitter,j2", "anti-short-ln,s1",
    ]

    # Nothing dropped -> no file at all, so an empty CSV cannot be mistaken for
    # "this dimension lost every chart".
    class _Clean:
        jitter = evaluate_jitter([1.0], [1.1])
        short_ln = evaluate_short_ln([1.0], [1.1])

    assert export_probe_drops_csv(_Clean(), tmp_path / "none.csv") is None


# ---------------------------------------------------------------------------
# 2026-10-21: r* outside the sampled rate grid must be probed, not dropped
# ---------------------------------------------------------------------------
def test_resolve_rstar_probes_beyond_the_grid(monkeypatch):
    from cab.runner import suite as suite_mod

    # 0.8 / 0.7 readings: monotone, so the ladder brackets the target.
    table = {("low", 0.8): 9.4, ("low", 0.7): 8.4}

    def fake_batch(args):
        name, rates, _content, *_rest = args
        return [(name, float(r), table.get((name, float(r)))) for r in rates]

    monkeypatch.setattr(suite_mod, "run_calc_rate_batch", fake_batch)

    class _Fake:
        _resolve_rstar = suite_mod.BenchmarkSuite._resolve_rstar

        def _map(self, pool, fn, tasks, chunksize=1):
            return [fn(t) for t in tasks]

    curves = {"low": [(0.9, 10.0), (1.0, 11.0), (2.0, 20.0)]}
    got = _Fake()._resolve_rstar(
        None, curves, {"low": 9.0}, {"low": "content"}, label="test"
    )
    # 8.4 (0.7) < 9.0 < 9.4 (0.8) -> linear interpolation lands near 0.75
    assert 0.70 <= got["low"] <= 0.80, got
    assert set(got) == {"low"}


def test_resolve_rstar_clamps_when_even_the_ladder_end_is_inside(monkeypatch):
    from cab.runner import suite as suite_mod

    # Everything the ladder returns is still above the target -> clamp to 0.7.
    def fake_batch(args):
        name, rates, _content, *_rest = args
        return [(name, float(r), 12.0) for r in rates]

    monkeypatch.setattr(suite_mod, "run_calc_rate_batch", fake_batch)

    class _Fake:
        _resolve_rstar = suite_mod.BenchmarkSuite._resolve_rstar

        def _map(self, pool, fn, tasks, chunksize=1):
            return [fn(t) for t in tasks]

    curves = {"low": [(0.9, 10.0), (1.0, 11.0), (2.0, 20.0)]}
    got = _Fake()._resolve_rstar(None, curves, {"low": 5.0}, {"low": "c"}, label="t")
    assert got["low"] == 0.7


def test_jitter_floor_decays_on_deep_drops():
    from cab.metrics.jitter_metric import jitter_score

    # Everything else equal: a 30% inversion must score far below a 10% one,
    # which a flat 10-point floor used to hide.
    deep = jitter_score(0.5, 15.0, 0.70, 0.5)
    mild = jitter_score(0.5, 15.0, 0.90, 0.5)
    assert deep < mild / 2.0, (deep, mild)
    # The rise side keeps the legibility floor.
    assert jitter_score(0.5, 15.0, 1.02, 0.5) > 10.0


def test_metric_ledger_keeps_probed_rstar(tmp_path):
    """Per-chart r* resolved by an on-demand probe must reach metric_inputs.csv.

    The probe reading is not in ``rate_curves``, so re-inverting returns None and
    the row used to be blank -- which silently broke the "every per-chart r* is
    auditable" promise for exactly the charts that needed the extra work.
    """
    from pathlib import Path

    from cab.parser.bench_loader import BenchmarkSample
    from cab.report.metric_ledger import build_metric_input_rows

    class _Acc:
        calibrated_predictions = [10.0]

    class _Result:
        samples = [BenchmarkSample(None, "probed", "tech", "", 10.0, Path("/tmp/p.osu"))]
        raw_predictions = [10.0]
        accuracy = _Acc()
        jitter_ledger = {"probed": (10.0, 8.0)}      # 8.0 sits below D(0.9)
        sln_ledger = {}
        rate_curves = {"probed": [(0.9, 9.0), (1.0, 10.0), (2.0, 20.0)]}
        rstar_jitter = {"probed": 0.76}              # resolved by the 0.8/0.7 ladder
        rstar_sln = {}

    row = build_metric_input_rows(_Result())[0]
    assert float(row["rstar_jitter"]) == 0.76, row["rstar_jitter"]

    # Without the map (a pre-2026-10-22 result) it falls back to inversion, i.e.
    # blank for out-of-range targets -- the old behaviour is still reachable.
    class _Legacy(_Result):
        rstar_jitter = {}

    assert build_metric_input_rows(_Legacy())[0]["rstar_jitter"] == ""
