"""Per-map predictions CSV export.

Writes one row per evaluated beatmap, pairing the algorithm's own reading with
the benchmark ground truth. The layout is deliberately column-compatible with
``samples/4k/uuzs_bench.csv`` on the identity columns (``path``, ``name``,
``pattern``, ``subPattern``), so the two files can be joined directly on
``path`` without any renaming.

Column semantics
----------------
``path``
    Beatmap location relative to ``samples/4k/`` — the exact same convention
    used by the ``path`` column of ``uuzs_bench.csv``. Portable across machines.
``name``
    Beatmap display name as recorded in the dataset.
``pattern`` / ``subPattern``
    Dataset taxonomy labels.
``expected``
    Ground-truth Numeric Difficulty (ND) value, i.e. the reference reading.
``got``
    The algorithm's own raw reading, exactly as the algorithm produced it,
    in whatever native scale that algorithm uses. No calibration is applied,
    so for an algorithm with a non-ND scale this is a raw score, not a
    difficulty on the benchmark's scale.
``numericdifficulty``
    That same reading converted onto the Numeric Difficulty scale (ND: the
    benchmark's default ground-truth ruler, Alpha 11.0 … Theta 18.0),
    i.e. the post-calibration value. This is the value the Ground-Truth
    Accuracy dimension scores against, so ``numericdifficulty - expected``
    reproduces the reported RMSE exactly. For algorithms whose native output
    already is ND (``scale_type`` of ``nd``) calibration is an identity
    transform, so this column equals ``got``.
``delta``
    ``numericdifficulty - expected``. Positive means the algorithm overrates the map.
``deltaAbs``
    ``abs(delta)``, provided so downstream tooling does not have to recompute it.

The final three columns carry the **declared-scope** reading. They are ``NaN``
for beatmaps the algorithm declared out of scope. With no scope declared every
row mirrors the columns above; with a scope active, in-scope beatmaps carry the
scope-only re-calibrated value (anchors fitted on the kept subset alone) and
out-of-scope beatmaps carry ``NaN``.

``ndinscope``
    The in-scope ND value: the reading re-calibrated against anchors fitted on
    the declared scope alone. ``NaN`` when the beatmap is out of scope.
``deltainscope``
    ``ndinscope - expected``. ``NaN`` when out of scope.
``deltaAbsinscope``
    ``abs(deltainscope)``. ``NaN`` when out of scope.
"""

from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from cab.parser.bench_loader import BenchmarkSample
from cab.report._io import atomic_write_text

#: Column order shared with ``samples/4k/uuzs_bench.csv`` on the identity
#: columns, then extended with the prediction columns. Keep this stable — the
#: benchmark comparison notes and downstream tooling join on it.
PREDICTION_COLUMNS = [
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

#: Literal written for beatmaps outside the declared scope.
NAN_CELL = "NaN"

#: Anchor used to relativize beatmap paths. Mirrors ``bench_loader``, which
#: resolves ``uuzs_bench.csv``'s ``path`` column as ``samples/4k/<path>``.
_SAMPLES_ANCHOR = "samples/4k"


def _fmt(value: float) -> str:
    """Format a numeric cell without destroying reproducibility.

    Four decimals is the precision used elsewhere in the reports; trailing
    zeros are stripped so the file stays readable.

    Non-finite values are written as ``NaN`` / ``Inf`` (never as ``nan`` or a
    bare ``-0``), and ``-0.0`` is normalised to ``0`` so downstream string
    comparisons and joins cannot fail on a cosmetic sign.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return NAN_CELL
    if v != v:  # NaN
        return NAN_CELL
    if v == float("inf"):
        return "Inf"
    if v == float("-inf"):
        return "-Inf"
    if v == 0:
        v = 0.0
    text = f"{v:.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _relativize(file_path: Path) -> str:
    """Express a beatmap path relative to ``samples/4k/`` when possible.

    Falls back to the absolute path for beatmaps residing outside the benchmark
    tree, so no row is ever silently dropped.
    """
    raw = str(file_path).replace("\\", "/")
    marker = f"/{_SAMPLES_ANCHOR}/"
    if marker in raw:
        return raw.split(marker, 1)[1]
    if raw.startswith(f"{_SAMPLES_ANCHOR}/"):
        return raw[len(_SAMPLES_ANCHOR) + 1 :]
    # Outside the benchmark tree (custom --samples-root): an absolute path
    # cannot be joined against uuzs_bench.csv on another machine, so say so
    # instead of silently emitting a machine-specific column.
    import warnings

    warnings.warn(
        f"predictions path is outside {_SAMPLES_ANCHOR}/ and will be exported "
        f"as an absolute path (not portable): {raw}",
        RuntimeWarning,
        stacklevel=3,
    )
    return raw


def build_prediction_rows(
    samples: Sequence[BenchmarkSample],
    calibrated: Sequence[float],
    raw: Sequence[float] | None = None,
    in_scope_calibrated: Optional[Dict[str, float]] = None,
) -> List[dict]:
    """Pair samples with predictions into row dicts.

    ``samples`` and ``calibrated`` must be index-aligned — the runner guarantees
    this, since both derive from the same ordered accuracy pass.

    ``calibrated`` holds the readings converted onto the ND scale; it drives the
    ``numericdifficulty`` column and therefore ``delta``. ``raw`` holds the
    algorithm's own untouched output and drives ``got``; when omitted the
    calibrated value is reused, which is correct for native-ND algorithms where
    the two are identical by definition.

    ``in_scope_calibrated`` carries the **secondary**, scope-only reading, keyed
    by beatmap name (names are unique across the bundled dataset). ``None``
    means no scope was declared, so every row's ``*inscope`` columns mirror the
    global ones. A dict — possibly empty when the declaration kept nothing —
    means a scope is active: a sample whose name is absent from it is out of
    scope and gets ``NaN`` in those columns, while present samples carry the
    scope-only re-calibrated value.

    A length mismatch is treated as a programming error rather than a silent
    truncation, because a misaligned export would be quietly wrong.
    """
    if len(samples) != len(calibrated):
        raise ValueError(
            "predictions/samples length mismatch: "
            f"{len(calibrated)} predictions vs {len(samples)} samples"
        )
    if raw is not None and len(raw) != len(samples):
        raise ValueError(
            "raw predictions/samples length mismatch: "
            f"{len(raw)} raw predictions vs {len(samples)} samples"
        )

    scope_active = in_scope_calibrated is not None
    scope_lookup = in_scope_calibrated or {}

    rows: List[dict] = []
    for i, sample in enumerate(samples):
        expected = float(sample.expected)
        nd = float(calibrated[i])
        raw_reading = float(raw[i]) if raw is not None else nd
        delta = nd - expected

        if not scope_active:
            in_scope = nd
        elif sample.name in scope_lookup:
            in_scope = float(scope_lookup[sample.name])
        else:
            in_scope = math.nan

        if math.isnan(in_scope):
            in_scope_cells = (NAN_CELL, NAN_CELL, NAN_CELL)
        else:
            d_scope = in_scope - expected
            in_scope_cells = (_fmt(in_scope), _fmt(d_scope), _fmt(abs(d_scope)))

        rows.append(
            {
                "path": _relativize(sample.file_path),
                "name": sample.name,
                "pattern": sample.pattern,
                "subPattern": sample.sub_pattern,
                "expected": _fmt(expected),
                "got": _fmt(raw_reading),
                "numericdifficulty": _fmt(nd),
                "delta": _fmt(delta),
                "deltaAbs": _fmt(abs(delta)),
                "ndinscope": in_scope_cells[0],
                "deltainscope": in_scope_cells[1],
                "deltaAbsinscope": in_scope_cells[2],
            }
        )
    return rows


def write_predictions_csv(rows: Sequence[dict], output_path: str | Path) -> Path:
    """Write prediction rows to ``output_path`` and return the resolved path."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    # utf-8-sig: without a BOM, WPS/Excel opens the file as GBK and re-saving
    # it mangles every non-ASCII path (定时地狱 -> 瀹氭椂鍦扮嫳), after which
    # refresh_isacc can no longer join on `path`.
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=PREDICTION_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    # Atomic replace: a crash mid-write must not leave a half CSV that later
    # tooling reads as "fewer beatmaps".
    atomic_write_text(out_file, buffer.getvalue(), encoding="utf-8-sig")
    return out_file


def export_predictions_csv(
    samples: Sequence[BenchmarkSample],
    calibrated: Sequence[float],
    output_path: str | Path,
    raw: Sequence[float] | None = None,
    in_scope_calibrated: Optional[Dict[str, float]] = None,
) -> Path:
    """Convenience wrapper: build rows and write them in one call."""
    rows = build_prediction_rows(
        samples,
        calibrated,
        raw=raw,
        in_scope_calibrated=in_scope_calibrated,
    )
    return write_predictions_csv(rows, output_path)
