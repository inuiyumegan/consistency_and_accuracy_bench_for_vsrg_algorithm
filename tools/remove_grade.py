#!/usr/bin/env python3
"""Remove the composite letter grade (``score.grade``) from existing result artifacts.

The CAB scoring model no longer emits a composite letter grade (S/S+/A/B/C/D) —
the numeric CAB-Score is expressive enough on its own. This tool back-fills that
change into already-generated artifacts **without re-running any algorithm**:

* Markdown reports: drops the "综合评级 (Grade)" / "Grade" column from the
  executive scorecard and the multi-tier comparison table, and removes the
  "(评级: `X`)" suffix from per-tier bullet lines.
* JSON results: removes the ``grade`` key from ``score_summary`` (single-tier)
  and from each entry of ``tiers`` (multi-tier summary).

``immunity_grade`` (the short-LN robustness verdict, e.g.
"F (Brute-Force LN-to-Tap Stripping)") is a *diagnostic* of dimension 5, not a
composite grade, and is deliberately preserved.

Idempotent: running it twice changes nothing on the second pass.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# --- Markdown: scorecard row (single-tier reports) --------------------------
# | 综合评级 (Grade) | 标准化 CAB-Score (总分) | ... |
_SCORECARD_HEADER = re.compile(
    r"^\| 综合评级 \(Grade\) \| (标准化 CAB-Score \(总分\) \|.*)$"
)
_SCORECARD_SEP = re.compile(r"^\| :---: \| :---: \|(.*)$")
_SCORECARD_DATA = re.compile(r"^\| \*\*`[^`]+`\*\* \| (\*\*`[\d.]+`\*\* / 100 \|.*)$")
_SCORECARD_DATA_NA = re.compile(r"^\| \*\*`N/A`\*\* \| (\*\*`N/A`\*\* \|.*)$")

# --- Markdown: multi-tier comparison table ----------------------------------
_TIER_HEADER = re.compile(
    r"^(\| 评测档位 \(Tier\) \| 难度区间 \(Range\) \| 样本数 \(Maps\) \|) 综合评级 \|(.*)$"
)
_TIER_DATA = re.compile(
    r"^(\| \*\*[^*]+\*\* \| [^|]+ \| \d+ \|) \*\*`[^`]+`\*\* \|(.*)$"
)
_TIER_DATA_NA = re.compile(
    r"^(\| \*\*[^*]+\*\* \| [^|]+ \| \d+ \|) \*\*`N/A`\*\* \|(.*)$"
)

# --- Markdown: per-tier bullet ---------------------------------------------
_BULLET_GRADE = re.compile(r" \(评级: \*\*`[^`]+`\*\*\)")


def _process_markdown(path: Path, dry_run: bool) -> int:
    text = path.read_text(encoding="utf-8")
    lines = text.split("\n")
    out: list[str] = []
    changes = 0

    for line in lines:
        m = _SCORECARD_HEADER.match(line)
        if m:
            out.append("| " + m.group(1)); changes += 1; continue
        m = _SCORECARD_DATA.match(line)
        if m:
            out.append("| " + m.group(1)); changes += 1; continue
        m = _SCORECARD_DATA_NA.match(line)
        if m:
            out.append("| " + m.group(1)); changes += 1; continue
        m = _TIER_HEADER.match(line)
        if m:
            out.append(m.group(1) + m.group(2)); changes += 1; continue
        m = _TIER_DATA.match(line)
        if m:
            out.append(m.group(1) + m.group(2)); changes += 1; continue
        m = _TIER_DATA_NA.match(line)
        if m:
            out.append(m.group(1) + m.group(2)); changes += 1; continue
        if _BULLET_GRADE.search(line):
            out.append(_BULLET_GRADE.sub("", line)); changes += 1; continue
        out.append(line)

    if changes and not dry_run:
        path.write_text("\n".join(out), encoding="utf-8")
    return changes


def _process_json(path: Path, dry_run: bool) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    changes = 0

    ss = data.get("score_summary")
    if isinstance(ss, dict) and "grade" in ss:
        ss.pop("grade", None); changes += 1

    tiers = data.get("tiers")
    if isinstance(tiers, dict):
        for entry in tiers.values():
            if isinstance(entry, dict) and "grade" in entry:
                entry.pop("grade", None); changes += 1

    if changes and not dry_run:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--dry-run", action="store_true", help="preview only")
    parser.add_argument(
        "roots", nargs="*", default=None,
        help="directories to scan (default: every results_*/ in cwd)",
    )
    args = parser.parse_args()

    cwd = Path.cwd()
    if args.roots:
        dirs = [Path(p) for p in args.roots]
    else:
        dirs = sorted(d for d in cwd.iterdir() if d.is_dir() and d.name.startswith("results"))

    total_files = 0
    total_changes = 0
    for root in dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            suffix = path.suffix.lower()
            if suffix == ".md":
                n = _process_markdown(path, args.dry_run)
            elif suffix == ".json" and "multi_tier" not in path.name and "benchmark_result" not in path.name:
                continue
            elif suffix == ".json":
                n = _process_json(path, args.dry_run)
            else:
                continue
            if n:
                total_files += 1
                total_changes += n
                print(f"{'[dry-run] ' if args.dry_run else ''}{path}: {n} edit(s)")

    verb = "would change" if args.dry_run else "changed"
    print(f"\n{verb} {total_changes} location(s) across {total_files} file(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
