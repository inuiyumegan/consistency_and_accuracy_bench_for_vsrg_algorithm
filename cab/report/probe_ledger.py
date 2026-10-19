"""Per-map probe ledger CSVs: the raw inputs behind the dynamic dimensions.

``predictions.csv`` has always carried the full per-map ACC payload, which is
why the accuracy dimension can be re-derived and audited offline.  The dynamic
probes (rate sweep, jitter, short-LN) kept their per-map readings only in
memory: the JSON artefacts store aggregates plus a ``worst_maps`` top-10 list,
so questions such as "which clock rate would reproduce this jittered reading"
were unanswerable from disk.

These exports close that gap.  They are pure projections of data the run has
already computed -- no metric is recomputed and no score changes.

Files
-----
``rate_curves.csv``
    One row per (beatmap, clock rate): the full ``D(r)`` curve that both the
    anti-inversion (AIN) and local-continuity (CT) dimensions aggregate.
``jitter_ledger.csv``
    One row per beatmap: original reading, jittered reading, and the delta.
``short_ln_ledger.csv``
    One row per beatmap: original reading, short-LN-injected reading, delta.

All three join to ``predictions.csv`` and ``samples/4k/uuzs_bench.csv`` on
``path``.  ``difficulty`` / ``orig`` / ``perturbed`` hold the algorithm's raw
reading in its own native scale, exactly as ``got`` does in
``predictions.csv`` -- no ND calibration is applied.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cab.report.predictions_csv import _relativize

RATE_COLUMNS: Tuple[str, ...] = (
    "path",
    "name",
    "pattern",
    "subPattern",
    "expected",
    "rate",
    "difficulty",
)

LEDGER_COLUMNS: Tuple[str, ...] = (
    "path",
    "name",
    "pattern",
    "subPattern",
    "expected",
    "orig",
    "perturbed",
    "delta",
    "rel_delta_pct",
)


def _fmt(value: Optional[float], digits: int = 6) -> str:
    if value is None:
        return "NaN"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "NaN"
    if math.isnan(number):
        return "NaN"
    return f"{number:.{digits}f}"


def _sample_index(samples: Sequence[Any] | None) -> Dict[str, Any]:
    return {s.name: s for s in (samples or [])}


def _identity(sample: Any) -> Dict[str, str]:
    """Identity columns, column-compatible with ``predictions.csv``."""
    return {
        "path": _relativize(sample.file_path),
        "name": sample.name,
        "pattern": sample.pattern,
        "subPattern": sample.sub_pattern,
        "expected": _fmt(sample.expected, 4),
    }


def _write(rows: List[Dict[str, str]], columns: Iterable[str], output_path: Path) -> Path:
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8", newline="") as fp:
        writer = csv.DictWriter(fp, fieldnames=list(columns))
        writer.writeheader()
        writer.writerows(rows)
    return out_file


def export_rate_curves_csv(result: Any, output_path: Path) -> Optional[Path]:
    """Write ``D(r)`` for every beatmap scanned by the rate probe.

    Sub-tiers do not evaluate the rate sweep, so their ``rate_curves`` is empty
    and this returns ``None`` (no file is written) rather than an empty CSV.
    """
    curves: Dict[str, Sequence[Tuple[float, float]]] = getattr(result, "rate_curves", None) or {}
    if not curves:
        return None
    index = _sample_index(getattr(result, "samples", None))

    rows: List[Dict[str, str]] = []
    for name in sorted(curves):
        sample = index.get(name)
        if sample is None:
            continue
        identity = _identity(sample)
        for rate, difficulty in sorted(curves[name], key=lambda point: point[0]):
            row = dict(identity)
            row["rate"] = f"{float(rate):.3f}"
            row["difficulty"] = _fmt(difficulty)
            rows.append(row)

    if not rows:
        return None
    return _write(rows, RATE_COLUMNS, output_path)


def _export_pair_ledger(
    ledger: Optional[Dict[str, Tuple[float, float]]],
    samples: Sequence[Any] | None,
    output_path: Path,
) -> Optional[Path]:
    """Shared writer for the jitter and short-LN ledgers."""
    if not ledger:
        return None
    index = _sample_index(samples)

    rows: List[Dict[str, str]] = []
    for name in sorted(ledger):
        sample = index.get(name)
        if sample is None:
            continue
        orig, perturbed = ledger[name]
        try:
            orig_f = float(orig)
            pert_f = float(perturbed)
        except (TypeError, ValueError):
            continue
        delta = pert_f - orig_f
        rel_pct = (delta / abs(orig_f) * 100.0) if abs(orig_f) > 1e-9 else float("nan")

        row = _identity(sample)
        row["orig"] = _fmt(orig_f)
        row["perturbed"] = _fmt(pert_f)
        row["delta"] = _fmt(delta)
        row["rel_delta_pct"] = _fmt(rel_pct, 4)
        rows.append(row)

    if not rows:
        return None
    return _write(rows, LEDGER_COLUMNS, output_path)


def export_jitter_ledger_csv(result: Any, output_path: Path) -> Optional[Path]:
    """Write the per-beatmap original/jittered pair behind the AJ dimension."""
    return _export_pair_ledger(
        getattr(result, "jitter_ledger", None),
        getattr(result, "samples", None),
        output_path,
    )


def export_short_ln_ledger_csv(result: Any, output_path: Path) -> Optional[Path]:
    """Write the per-beatmap original/short-LN-injected pair behind ASLN."""
    return _export_pair_ledger(
        getattr(result, "sln_ledger", None),
        getattr(result, "samples", None),
        output_path,
    )


def export_probe_ledgers(result: Any, output_dir: Path) -> List[Path]:
    """Write all three ledgers for one run into ``output_dir``.

    Returns only the files actually written, so callers can report them
    without having to know which dimensions ran.
    """
    out_dir = Path(output_dir)
    written: List[Path] = []
    for exporter, filename in (
        (export_rate_curves_csv, "rate_curves.csv"),
        (export_jitter_ledger_csv, "jitter_ledger.csv"),
        (export_short_ln_ledger_csv, "short_ln_ledger.csv"),
    ):
        path = exporter(result, out_dir / filename)
        if path is not None:
            written.append(path)
    return written
