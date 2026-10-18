"""Integrity guards for ``samples/4k/uuzs_bench.csv``.

The manifest is hand-edited (frequently in a spreadsheet app), so these tests
target the failure modes that have actually bitten us:

* a spreadsheet re-save on a non-UTF-8 locale mangles CJK / accented paths into
  mojibake — caught here because those paths stop resolving to a real file;
* a row deleted (or added) without touching the sample directory — caught by
  the set comparison between manifest and disk;
* a duplicated row, which would silently double-weight a chart in ACC.
"""

import csv
from pathlib import Path

import pytest

SAMPLES = Path("samples/4k")
CSV_PATH = SAMPLES / "uuzs_bench.csv"
COLUMNS = ["path", "name", "pattern", "subPattern", "expected"]

pytestmark = pytest.mark.skipif(
    not CSV_PATH.exists(),
    reason="requires samples/4k benchmark dataset",
)


def _read():
    with CSV_PATH.open(encoding="utf-8-sig", newline="") as fp:
        reader = csv.reader(fp)
        return next(reader), list(reader)


def test_header_is_the_expected_five_columns():
    header, _ = _read()
    assert header == COLUMNS


def test_rows_have_exactly_five_fields():
    _, rows = _read()
    bad = [i + 2 for i, row in enumerate(rows) if len(row) != len(COLUMNS)]
    assert bad == [], f"rows with a wrong field count: {bad}"


def test_every_row_resolves_to_a_real_file():
    """Also the mojibake detector: a mangled path cannot resolve."""
    _, rows = _read()
    missing = [row[0] for row in rows if not (SAMPLES / row[0]).is_file()]
    assert missing == [], f"{len(missing)} manifest paths have no file: {missing[:5]}"


def test_every_sample_file_is_referenced():
    _, rows = _read()
    referenced = {row[0] for row in rows}
    on_disk = {p.relative_to(SAMPLES).as_posix() for p in SAMPLES.glob("*/*.osu")}
    orphans = sorted(on_disk - referenced)
    assert orphans == [], f"{len(orphans)} sample files are orphaned: {orphans[:5]}"


def test_paths_are_unique():
    _, rows = _read()
    seen, dupes = set(), []
    for row in rows:
        if row[0] in seen:
            dupes.append(row[0])
        seen.add(row[0])
    assert dupes == [], f"duplicated paths: {dupes[:5]}"


def test_expected_values_parse_as_numbers():
    _, rows = _read()
    bad = []
    for row in rows:
        try:
            float(row[4])
        except ValueError:
            bad.append((row[0], row[4]))
    assert bad == [], f"non-numeric expected values: {bad[:5]}"
