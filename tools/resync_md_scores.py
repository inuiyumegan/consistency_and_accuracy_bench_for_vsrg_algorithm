#!/usr/bin/env python3
"""Re-sync scores inside existing Markdown reports with their sibling JSON.

Background: when the ACC metric was re-specified (balanced stratified RMSE) an
earlier pass rewrote ``score_summary`` inside the JSON results but did **not**
touch the Markdown reports, leaving the two artifacts disagreeing on the total
and per-dimension scores. This tool back-fills the numeric cells in the already
rendered Markdown from the authoritative JSON — **without re-running any
algorithm**.

What it rewrites:

* single-tier report (``CAB_BENCHMARK_REPORT.md``): the executive scorecard row
  (total + the six dimension scores), plus the ``键型加权 RMSE`` bullet whose
  value is re-derived by ``recompute_scores.py``.
* multi-tier report (``CAB_MULTI_TIER_SUMMARY.md``): the four rows of the
  executive comparison table (total + dimension scores per tier), plus the
  per-tier ``综合得分`` bullets.

It only replaces numbers inside scorecard rows and score bullets; all narrative
text, tables of raw metrics, and images are left untouched. Idempotent.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

_TIER_IDS = ["all", "number", "greek", "inhuman"]
_TIER_DIRS = ["all", "number_dan", "greek_dan", "inhuman_dan"]
_TIER_ORDER = [
    ("全域段 (All Range)", r"\$\ge -2\.5\$"),
    ("数字段 (Number Dan)", r"\$\[-2\.5, 10\.5\)\$"),
    ("字母段 (Greek Dan)", r"\$\[10\.5, 15\.5\]\$"),
    ("非人类段 (Inhuman Dan)", r"\$> 15\.5\$"),
]

# Dimension order as emitted by the report renderer.
_DIM_ORDER = [
    "Ground-Truth Accuracy",
    "Anti-Rate-Inversion",
    "Anti-Short LN",
    "Local Continuity",
    "Computation Speed",
    "Anti-Jitter",
]


def _fmt(value) -> str:
    if value is None:
        return "-"
    return f"{value:.1f}"


def _scorecard_row(dims: dict, total: float) -> str:
    cells = " | ".join(_fmt(dims.get(name)) for name in _DIM_ORDER)
    return f"| **`{total:.1f}`** / 100 | {cells} |"


def _sync_single_tier(md_path: Path, json_path: Path, dry_run: bool) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    ss = data.get("score_summary")
    if not ss:
        return 0
    dims = ss.get("dimensions") or {}
    total = ss.get("total_score")
    if total is None:
        return 0

    text = md_path.read_text(encoding="utf-8")
    new_text = text

    # 1) Executive scorecard row.
    pattern = re.compile(r"^\| \*\*`[\d.]+`\*\* / 100 \|.*\|$", re.MULTILINE)
    if pattern.search(new_text):
        new_text = pattern.sub(_scorecard_row(dims, total), new_text, count=1)

    # 2) The balanced stratified RMSE bullet ("键型加权 RMSE").  Its value is
    #    re-derived by ``recompute_scores.py`` and lives in the accuracy block;
    #    fall back to ``dimension_1_accuracy`` for older JSON vintages.
    wrmse = (ss.get("accuracy") or {}).get("weighted_stratified_rmse")
    if wrmse is None:
        wrmse = (data.get("dimension_1_accuracy") or {}).get("weighted_stratified_rmse")
    if wrmse is not None:
        rmse_re = re.compile(
            r"(键型加权 RMSE \(Weighted Stratified RMSE\)\*\*: `)[\d.]+(`)"
        )
        new_text = rmse_re.sub(lambda m: f"{m.group(1)}{float(wrmse):.4f}{m.group(2)}", new_text)

    if new_text == text:
        return 0
    if not dry_run:
        md_path.write_text(new_text, encoding="utf-8")
    return 1


def _sync_multi_tier(md_path: Path, json_path: Path, dry_run: bool) -> int:
    data = json.loads(json_path.read_text(encoding="utf-8"))
    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        return 0

    lines = md_path.read_text(encoding="utf-8").split("\n")
    changes = 0
    for idx, line in enumerate(lines):
        if not line.startswith("| **") or "/ 100" not in line:
            continue
        for tier_key, (name, _range) in zip(_TIER_IDS, _TIER_ORDER):
            if f"**{name}**" not in line:
                continue
            entry = tiers.get(tier_key)
            if not isinstance(entry, dict):
                break
            total = entry.get("total_score")
            if total is None:
                break
            dims = entry.get("dimension_scores") or {}

            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            # Layout: Tier | Range | Maps | CAB-Score | ACC | AIN | ASLN | CT | SPD | AJ | CalibRMSE
            if len(cells) < 10:
                break
            new_cells = list(cells)
            new_cells[3] = f"**`{total:.1f}`** / 100"
            for offset, dim in enumerate(_DIM_ORDER):
                new_cells[4 + offset] = _fmt(dims.get(dim))
            new_line = "| " + " | ".join(new_cells) + " |"
            if new_line != line:
                lines[idx] = new_line
                changes += 1
            break

    if changes and not dry_run:
        md_path.write_text("\n".join(lines), encoding="utf-8")

    # Also resync the per-tier "- **综合得分**: **`X` / 100**" bullet lines,
    # which live in the section 2 narrative of the same multi-tier report. The
    # authoritative value comes from each tier's own sub-folder JSON.
    changes += _sync_tier_bullets(md_path, md_path.parent, dry_run)
    return changes


def _sync_tier_bullets(md_path: Path, root: Path, dry_run: bool) -> int:
    """Rewrite ``- **综合得分**: **`X` / 100**`` lines from per-tier JSON."""
    plan: list[tuple[str, float]] = []
    for tier_key, dir_name in zip(_TIER_IDS, _TIER_DIRS):
        sub_json = root / dir_name / "cab_benchmark_result.json"
        if not sub_json.is_file():
            continue
        try:
            ss = json.loads(sub_json.read_text(encoding="utf-8")).get("score_summary") or {}
        except json.JSONDecodeError:
            continue
        total = ss.get("total_score")
        if ss.get("is_out_of_domain") or total is None:
            continue
        plan.append((tier_key, total))

    lines = md_path.read_text(encoding="utf-8").split("\n")
    bullet_re = re.compile(r"^- \*\*综合得分\*\*: \*\*`([\d.]+)` / 100\*\*$")
    changes = 0
    cursor = 0
    for idx, line in enumerate(lines):
        m = bullet_re.match(line)
        if not m:
            continue
        if cursor >= len(plan):
            break
        _key, total = plan[cursor]
        cursor += 1
        new_line = f"- **综合得分**: **`{total:.1f}` / 100**"
        if new_line == line:
            continue
        lines[idx] = new_line
        changes += 1

    if changes and not dry_run:
        md_path.write_text("\n".join(lines), encoding="utf-8")
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument("roots", nargs="*", default=None,
                        help="directories to scan (default: results*/ in cwd)")
    args = parser.parse_args()

    cwd = Path.cwd()
    if args.roots:
        dirs = [Path(p) for p in args.roots]
    else:
        dirs = sorted(d for d in cwd.iterdir() if d.is_dir() and d.name.startswith("results"))

    files = 0
    edits = 0
    for root in dirs:
        if not root.is_dir():
            continue

        # multi-tier summary
        mt_md = root / "CAB_MULTI_TIER_SUMMARY.md"
        mt_json = root / "cab_multi_tier_summary.json"
        if mt_md.is_file() and mt_json.is_file():
            n = _sync_multi_tier(mt_md, mt_json, args.dry_run)
            if n:
                files += 1; edits += n
                print(f"{'[dry-run] ' if args.dry_run else ''}{mt_md}: {n} row(s)")

        # each tier folder (incl. top-level mirror)
        for sub in [""] + _TIER_DIRS:
            base = root / sub if sub else root
            md = base / "CAB_BENCHMARK_REPORT.md"
            js = base / "cab_benchmark_result.json"
            if md.is_file() and js.is_file():
                n = _sync_single_tier(md, js, args.dry_run)
                if n:
                    files += 1; edits += n
                    print(f"{'[dry-run] ' if args.dry_run else ''}{md}: scorecard resynced")

    verb = "would update" if args.dry_run else "updated"
    print(f"\n{verb} {edits} location(s) across {files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
