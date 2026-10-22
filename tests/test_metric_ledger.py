"""The metric ledger must be a sufficient statistic for the score.

If these tests pass, changing a *conversion* constant (β, RMSE decay, Hill
anchors, a dimension's 0-100 curve) can be evaluated offline against an
existing run via ``selfcheck/tools/rescore_from_ledger.py`` instead of
re-running ``--mode standard`` for every algorithm.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cab.metrics.accuracy import evaluate_accuracy  # noqa: E402
from cab.metrics.continuity import evaluate_continuity  # noqa: E402
from cab.metrics.inversion import evaluate_rate_inversion  # noqa: E402
from cab.metrics.jitter_metric import evaluate_jitter  # noqa: E402
from cab.metrics.scoring import calculate_cab_score  # noqa: E402
from cab.metrics.short_ln_metric import evaluate_short_ln  # noqa: E402
from cab.metrics.speed_metric import evaluate_speed  # noqa: E402
from cab.report.metric_ledger import export_metric_ledger  # noqa: E402
from cab.report.probe_ledger import export_rate_curves_csv  # noqa: E402


def _load_rescore_module():
    path = ROOT / "selfcheck/tools/rescore_from_ledger.py"
    spec = importlib.util.spec_from_file_location("rescore_from_ledger", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _build_run():
    """A small but complete run: 4 charts, rate sweep, both probes, speed."""
    names = ["jack/A.osu", "jack/B.osu", "tech/C.osu", "speed/D.osu"]
    patterns = ["jack", "jack", "tech", "speed"]
    expected = [10.0, 11.0, 13.0, 15.0]
    raw = [9.6, 10.9, 13.4, 14.2]

    samples = [
        SimpleNamespace(
            name=Path(rel).stem,
            pattern=pat,
            sub_pattern="dense chordjack" if pat == "jack" else pat,
            expected=exp,
            file_path=Path("samples/4k") / rel,
        )
        for rel, pat, exp in zip(names, patterns, expected)
    ]

    accuracy = evaluate_accuracy(samples, raw)

    curves = {}
    jitter_ledger = {}
    sln_ledger = {}
    for sample, base in zip(samples, raw):
        curves[sample.name] = [(r, base * (1.0 + 0.28 * (r - 1.0))) for r in (1.0, 1.1, 1.2, 1.3, 1.5, 1.8)]
        jitter_ledger[sample.name] = (base, base * 1.004)
        sln_ledger[sample.name] = (base, base * 1.06)

    inversion = evaluate_rate_inversion(curves)
    continuity = evaluate_continuity(curves)
    jitter = evaluate_jitter(
        [pair[0] for pair in jitter_ledger.values()],
        [pair[1] for pair in jitter_ledger.values()],
        arrangement_sensitivity_pct=14.0,
    )
    short_ln = evaluate_short_ln(
        [pair[0] for pair in sln_ledger.values()],
        [pair[1] for pair in sln_ledger.values()],
        ho_delta_pct=18.0,
    )
    speed = evaluate_speed([(120.0, 100.0), (140.0, 110.0), (160.0, 120.0)])

    score = calculate_cab_score(
        accuracy=accuracy,
        inversion=inversion,
        continuity=continuity,
        jitter=jitter,
        short_ln=short_ln,
        speed=speed,
    )
    return SimpleNamespace(
        algorithm_name="Fixture",
        scale_type="auto",
        tier_id="all",
        samples=samples,
        raw_predictions=raw,
        accuracy=accuracy,
        inversion=inversion,
        continuity=continuity,
        jitter=jitter,
        short_ln=short_ln,
        speed=speed,
        score=score,
        rate_curves=curves,
        jitter_ledger=jitter_ledger,
        sln_ledger=sln_ledger,
    )


@pytest.fixture()
def ledger_dir(tmp_path: Path) -> Path:
    run = _build_run()
    written = export_metric_ledger(run, tmp_path)
    export_rate_curves_csv(run, tmp_path / "rate_curves.csv")
    assert {p.name for p in written} == {
        "metric_inputs.csv",
        "speed_samples.csv",
        "metric_scalars.csv",
    }
    return tmp_path


def test_ledger_reproduces_every_dimension(ledger_dir: Path):
    rescore = _load_rescore_module()
    ledger = rescore.Ledger(ledger_dir)
    rebuilt = rescore.rescore(ledger)["score"]
    original = _build_run().score

    for key in ("s_accuracy", "s_inversion", "s_short_ln", "s_continuity", "s_speed", "s_jitter"):
        before, after = getattr(original, key), getattr(rebuilt, key)
        assert (before is None) == (after is None), key
        if before is not None:
            assert after == pytest.approx(before, abs=0.005), key
    assert rebuilt.total_score == pytest.approx(original.total_score, abs=0.005)


def test_ledger_columns_are_stable(ledger_dir: Path):
    import csv

    with open(ledger_dir / "metric_inputs.csv", encoding="utf-8-sig", newline="") as fp:
        header = next(csv.reader(fp))
    # The rescore tool addresses these by name; renaming is a schema change.
    for column in ("path", "name", "expected", "raw_prediction", "jitter_orig", "rstar_jitter"):
        assert column in header
    assert (ledger_dir / "metric_inputs.csv").read_bytes().startswith(b"\xef\xbb\xbf")


def test_empty_run_writes_no_ledger(tmp_path: Path):
    empty = SimpleNamespace(samples=[], accuracy=SimpleNamespace(calibrated_predictions=[]))
    assert export_metric_ledger(empty, tmp_path) == []
