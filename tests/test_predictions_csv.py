"""Unit tests for the per-map predictions CSV export.

The export must stay join-compatible with ``samples/4k/uuzs_bench.csv`` on the
identity columns, because the benchmark comparison workflow joins the two files
on ``path``. These tests pin that contract, the raw/calibrated split, and the
declared-scope columns.
"""

from pathlib import Path

import pytest

from cab.parser.bench_loader import BenchmarkSample
from cab.report.predictions_csv import (
    PREDICTION_COLUMNS,
    build_prediction_rows,
    export_predictions_csv,
    write_predictions_csv,
)


def _sample(name: str, pattern: str, expected: float, sub: str = "stream tech") -> BenchmarkSample:
    return BenchmarkSample(
        bid=None,
        name=name,
        pattern=pattern,
        sub_pattern=sub,
        expected=expected,
        file_path=Path(f"/repo/samples/4k/{pattern}/{name}.osu"),
    )


def test_column_order_is_stable():
    """Downstream joins and docs depend on this exact order."""
    assert PREDICTION_COLUMNS == [
        "path",
        "name",
        "pattern",
        "subPattern",
        "expected",
        "got",
        "numericdifficulty",
        "delta",
        "deltaAbs",
        "ndinscope",
        "deltainscope",
        "deltaAbsinscope",
    ]


def test_path_is_relative_to_samples_4k():
    """``path`` must match the convention of uuzs_bench.csv's path column."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.5], raw=[12.3])
    assert rows[0]["path"] == "tech/Foo.osu"


def test_delta_and_delta_abs_are_consistent():
    s = _sample("Foo", "tech", 12.0)
    # Overrated by 0.5
    rows = build_prediction_rows([s], [12.5], raw=[12.1])
    assert rows[0]["delta"] == "0.5"
    assert rows[0]["deltaAbs"] == "0.5"

    # Underrated by 0.5 -> delta negative, deltaAbs still positive
    rows = build_prediction_rows([s], [11.5], raw=[11.1])
    assert rows[0]["delta"] == "-0.5"
    assert rows[0]["deltaAbs"] == "0.5"


def test_got_is_raw_and_numericdifficulty_is_calibrated():
    """``got`` carries the algorithm's own reading; ``numericdifficulty`` is the
    post-calibration value on the ND ruler. Under a real calibration they must
    not be conflated."""
    s = _sample("Foo", "tech", 12.0)
    # raw native scale gives 13.0 -> calibrated onto ND as 12.4
    rows = build_prediction_rows([s], [12.4], raw=[13.0])
    assert rows[0]["got"] == "13"
    assert rows[0]["numericdifficulty"] == "12.4"


def test_delta_is_measured_against_numericdifficulty():
    """delta must follow the calibrated column, since that is what ACC scores."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.4], raw=[100.0])
    # delta uses numericdifficulty (12.4) - expected (12.0), NOT the raw 100.0
    assert rows[0]["delta"] == "0.4"
    assert rows[0]["deltaAbs"] == "0.4"


def test_numericdifficulty_falls_back_to_got_when_raw_absent():
    """Native-ND algorithms pass no raw list; calibration is identity there."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.25], raw=None)
    assert rows[0]["got"] == rows[0]["numericdifficulty"] == "12.25"


def test_scope_columns_mirror_globals_without_a_declaration():
    """With no scope declared, the *inscope* columns equal the global ones."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.5], raw=[12.3])
    row = rows[0]
    assert row["ndinscope"] == row["numericdifficulty"] == "12.5"
    assert row["deltainscope"] == row["delta"] == "0.5"
    assert row["deltaAbsinscope"] == row["deltaAbs"] == "0.5"


def test_scope_columns_split_in_scope_and_nan():
    """With a scope active, in-scope rows carry the scope-only value and
    out-of-scope rows are NaN."""
    kept = _sample("Kept", "speed", 12.0)
    dropped = _sample("Dropped", "ln", 12.0)
    rows = build_prediction_rows(
        [kept, dropped],
        [12.6, 11.0],
        raw=[99.0, 99.0],
        in_scope_calibrated={"Kept": 12.1},
    )
    assert rows[0]["ndinscope"] == "12.1"
    assert rows[0]["deltainscope"] == "0.1"
    assert rows[0]["deltaAbsinscope"] == "0.1"
    assert rows[1]["ndinscope"] == "NaN"
    assert rows[1]["deltainscope"] == "NaN"
    assert rows[1]["deltaAbsinscope"] == "NaN"
    # The global columns are untouched by the scope reading.
    assert rows[1]["numericdifficulty"] == "11"


def test_empty_scope_dict_marks_everything_nan():
    """A declaration that kept nothing yields no secondary fit; every row is NaN."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.5], raw=[12.3], in_scope_calibrated={})
    assert rows[0]["ndinscope"] == "NaN"


def test_length_mismatch_raises():
    """A misaligned export would be silently wrong, so it must fail loudly."""
    s = _sample("Foo", "tech", 12.0)
    with pytest.raises(ValueError, match="length mismatch"):
        build_prediction_rows([s, s], [12.0])
    with pytest.raises(ValueError, match="raw predictions"):
        build_prediction_rows([s, s], [12.0, 12.0], raw=[12.0])


def test_export_roundtrip(tmp_path: Path):
    """The written file must parse back with the promised header."""
    import csv

    samples = [_sample("A", "tech", 12.0), _sample("B", "jack", 9.0, sub="dense chordjack")]
    out = export_predictions_csv(
        samples, [12.5, 8.8], output_path=tmp_path / "predictions.csv", raw=[12.2, 8.8]
    )
    assert out.exists()

    # utf-8-sig: the writer emits a BOM so spreadsheet apps do not mangle
    # non-ASCII paths on save.
    assert out.read_bytes().startswith(b"\xef\xbb\xbf")
    with out.open(encoding="utf-8-sig", newline="") as fp:
        reader = csv.DictReader(fp)
        assert reader.fieldnames == PREDICTION_COLUMNS
        rows = list(reader)

    assert len(rows) == 2
    assert rows[0]["path"] == "tech/A.osu"
    assert rows[1]["path"] == "jack/B.osu"
    assert rows[1]["subPattern"] == "dense chordjack"
    # raw reading vs calibrated ND value
    assert rows[0]["got"] == "12.2"
    assert rows[0]["numericdifficulty"] == "12.5"
    assert rows[1]["got"] == "8.8"
    assert rows[1]["numericdifficulty"] == "8.8"


def test_negative_zero_is_normalised(tmp_path: Path):
    """A -0.0 delta must not show a confusing minus sign."""
    s = _sample("Foo", "tech", 12.0)
    rows = build_prediction_rows([s], [12.0], raw=[12.0])
    assert rows[0]["delta"] == "0"
    assert rows[0]["deltaAbs"] == "0"


def test_write_empty_rows_still_emits_header(tmp_path: Path):
    out = write_predictions_csv([], tmp_path / "empty.csv")
    assert out.read_text(encoding="utf-8-sig").strip() == ",".join(PREDICTION_COLUMNS)


@pytest.mark.skipif(
    not Path("samples/4k/uuzs_bench.csv").exists(),
    reason="benchmark dataset not present",
)
def test_exported_paths_join_against_ground_truth():
    """Every exported path must exist in uuzs_bench.csv's path column."""
    import csv
    from cab.parser.bench_loader import BenchmarkDataset

    ds = BenchmarkDataset.load_from_csv("samples/4k/uuzs_bench.csv")
    rows = build_prediction_rows(ds.samples, [float(s.expected) for s in ds.samples])

    with open("samples/4k/uuzs_bench.csv", encoding="utf-8-sig") as fp:
        truth_paths = {r["path"] for r in csv.DictReader(fp)}

    exported = {r["path"] for r in rows}
    assert exported == truth_paths
