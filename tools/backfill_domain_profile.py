#!/usr/bin/env python3
"""One-off backfill: inject the programmatic domain profile into existing results.

Background
----------
The domain-profiling feature (``cab/report/profiling.py``) was added after the
existing ``results_*`` directories had already been produced.  Their JSON files
predate the ``stratified_counts`` / ``domain_profile`` fields, so their reports
lack the strengths/weaknesses section.

Re-running the whole benchmark just to obtain a derived judgment would be
wasteful: the profile depends only on data that is *already stored* in each
JSON file -- namely ``dimension_1_accuracy.stratified_rmse`` plus the per-pattern
slice counts in ``dataset_filters.sample_stats.patterns``.  Neither value
requires re-invoking the algorithm under test.

This script therefore *rebuilds derived artifacts only*:

* JSON  -- adds ``stratified_counts`` and ``domain_profile`` under
  ``dimension_1_accuracy`` (single-tier) and ``tiers[*].accuracy`` (multi-tier).
* MARKDOWN -- upgrades the 4-column pattern table to the current 6-column form
  (with sample counts, baseline ratio and verdict) and appends the
  "算法领域适配画像" section right after it.

Algorithm numbers (RMSE, scores, curves) are never touched.

Usage:
    python3 tools/backfill_domain_profile.py [--dry-run] [dir ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cab.report.markdown import _render_domain_profile  # noqa: E402
from cab.report.profiling import describe_pattern, profile_domains  # noqa: E402


TABLE_HEADER_OLD = "| 键型分类 (Pattern) | 样本数 (Samples) | 单键型 RMSE (ng) | 说明 |"
TABLE_HEADER_NEW = (
    "| 键型分类 (Pattern) | 样本数 (Samples) | 单键型 RMSE (ng) "
    "| 相对基线比值 | 领域判定 | 说明 |"
)
TABLE_SEP_OLD = "| :--- | :---: | :---: | :--- |"
TABLE_SEP_NEW = "| :--- | :---: | :---: | :---: | :---: | :--- |"
TABLE_TITLE = "#### 分键型准确性明细 (Pattern-Stratified Breakdown, 包含 Course)"
PROFILE_MARKER = "#### 算法领域适配画像 (Domain Fitness Profile, 程序化判定)"

# e.g.  | `jack` | - | **0.5471** | 常规单曲键型 |
_ROW_RE = re.compile(
    r"^\| `(?P<pat>[^`]+)` \| (?P<n>[^|]*)\| \*\*(?P<rmse>[0-9.]+)\*\* \| (?P<desc>[^|]*)\|$"
)


def _counts_for_tier(payload: dict) -> dict:
    """Extract per-pattern sample counts for a single-tier JSON payload."""
    stats = (payload.get("dataset_filters") or {}).get("sample_stats") or {}
    patterns = stats.get("patterns") or {}
    return {str(k).lower(): int(v) for k, v in patterns.items()}


def _update_json_accuracy(acc: dict, count_map: dict) -> None:
    """Add stratified_counts + domain_profile into an accuracy dict in place."""
    rmse = acc.get("stratified_rmse") or {}
    counts = {p.lower(): count_map[p.lower()] for p in rmse if p.lower() in count_map}
    prof = profile_domains(rmse, pattern_counts=counts or None)

    acc["stratified_counts"] = counts
    acc["domain_profile"] = {
        "baseline_rmse": round(prof.baseline_rmse, 4),
        "min_samples": prof.min_samples,
        "has_judged": prof.has_judged,
        "strengths": [v.pattern for v in prof.strengths],
        "weaknesses": [v.pattern for v in prof.weaknesses],
        "adequate": [v.pattern for v in prof.adequate],
        "insufficient": [v.pattern for v in prof.insufficient],
        "verdicts": [
            {
                "pattern": v.pattern,
                "rmse": round(v.rmse, 4),
                "samples": v.samples,
                "ratio": round(v.ratio, 4),
                "verdict": v.verdict,
            }
            for v in prof.verdicts
        ],
    }


def _rebuild_table_block(md_text: str, rmse: dict, counts: dict, algo_name: str) -> str:
    """Replace the old 4-column pattern table with the current 6-column + profile.

    Idempotent: if the file already carries the new table header (and therefore
    the profile section), it is returned unchanged.
    """
    if TABLE_TITLE not in md_text:
        return md_text  # nothing to patch

    # Already upgraded in a previous pass -> leave it alone.
    if TABLE_HEADER_NEW in md_text:
        return md_text

    lines = md_text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.strip() == TABLE_TITLE:
            profile = profile_domains(rmse, pattern_counts=counts or None)

            out.append(TABLE_TITLE)
            out.append(TABLE_HEADER_NEW)
            out.append(TABLE_SEP_NEW)
            for v in profile.verdicts:
                n_str = str(v.samples) if v.samples >= 0 else "-"
                out.append(
                    f"| `{v.pattern}` | {n_str} | **{v.rmse:.4f}** | {v.ratio:.2f}x "
                    f"| **{v.label}** | {describe_pattern(v.pattern)} |"
                )
            # Skip the original header / separator / data rows verbatim.
            i += 1
            while i < len(lines) and (
                lines[i].strip() in (TABLE_HEADER_OLD, TABLE_SEP_OLD)
                or _ROW_RE.match(lines[i].strip())
            ):
                i += 1
            out.append("")
            out.extend(_render_domain_profile(profile, algo_name=algo_name))
            continue
        out.append(line)
        i += 1
    return "\n".join(out)


def _patch_single_tier_report(md_path: Path, payload: dict, algo_name: str) -> bool:
    acc = payload.get("dimension_1_accuracy") or {}
    rmse = acc.get("stratified_rmse") or {}
    if not rmse:
        return False
    counts = _counts_for_tier(payload)
    text = md_path.read_text(encoding="utf-8")
    new_text = _rebuild_table_block(text, rmse, counts, algo_name)
    if new_text == text:
        return False
    md_path.write_text(new_text, encoding="utf-8")
    return True


def _patch_dir(root: Path, dry_run: bool, verbose: bool) -> dict:
    result = {"json": 0, "md": 0, "skipped": 0}
    root_json = root / "cab_benchmark_result.json"
    if not root_json.exists():
        return result

    with open(root_json, encoding="utf-8") as fp:
        payload = json.load(fp)
    algo_name = payload.get("algorithm", root.name)

    # --- JSON: root single-tier + every per-tier subdirectory ---
    targets: list[Path] = [root_json]
    for sub in sorted(root.glob("*/cab_benchmark_result.json")):
        targets.append(sub)

    for jpath in targets:
        with open(jpath, encoding="utf-8") as fp:
            data = json.load(fp)
        acc = data.get("dimension_1_accuracy")
        if not acc:
            result["skipped"] += 1
            continue
        _update_json_accuracy(acc, _counts_for_tier(data))

        # Multi-tier summary uses tiers[*].accuracy instead.
        for tier in (data.get("tiers") or {}).values():
            t_acc = tier.get("accuracy")
            if t_acc is not None:
                acc_counts = t_acc.get("stratified_counts") or {}
                if not acc_counts:
                    stats = (data.get("dataset_filters") or {}).get("sample_stats") or {}
                    acc_counts = {str(k).lower(): int(v) for k, v in (stats.get("patterns") or {}).items()}
                _update_json_accuracy(t_acc, acc_counts)

        if not dry_run:
            with open(jpath, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2, ensure_ascii=False)
        result["json"] += 1
        if verbose:
            print(f"  [json] {jpath}")

    # --- MARKDOWN: single-tier reports ---
    md_targets = [root / "CAB_BENCHMARK_REPORT.md"]
    md_targets += sorted(root.glob("*/CAB_BENCHMARK_REPORT.md"))
    for mpath in md_targets:
        jpath = mpath.parent / "cab_benchmark_result.json"
        if not jpath.exists():
            continue
        with open(jpath, encoding="utf-8") as fp:
            data = json.load(fp)
        if _patch_single_tier_report(mpath, data, algo_name) or dry_run:
            result["md"] += 1
            if verbose:
                print(f"  [md]   {mpath}")

    # --- MARKDOWN: multi-tier summary (append per-tier profile sections) ---
    multi_md = root / "CAB_MULTI_TIER_SUMMARY.md"
    multi_json = root / "cab_multi_tier_summary.json"
    if multi_md.exists() and multi_json.exists():
        with open(multi_json, encoding="utf-8") as fp:
            mdata = json.load(fp)
        # Older multi-tier JSON only carried an accuracy *summary*; pull the
        # full stratified_rmse back in from each tier's own sub-directory.
        _augment_multitier_from_subdirs(root, mdata)
        text = multi_md.read_text(encoding="utf-8")
        if PROFILE_MARKER not in text:
            text, added = _append_multitier_profiles(text, mdata, algo_name)
            if added and not dry_run:
                multi_md.write_text(text, encoding="utf-8")
            if added:
                result["md"] += 1
                if verbose:
                    print(f"  [md]   {multi_md} (multi-tier)")

    if not dry_run:
        with open(multi_json, "w", encoding="utf-8") as fp:
            json.dump(mdata, fp, indent=2, ensure_ascii=False)

    return result


def _augment_multitier_from_subdirs(root: Path, mdata: dict) -> None:
    """Backfill stratified_rmse/counts into tiers[*].accuracy from tier subdirs."""
    dir_map = {
        "all": "all",
        "number": "number_dan",
        "greek": "greek_dan",
        "inhuman": "inhuman_dan",
    }
    for tier_id, tier in (mdata.get("tiers") or {}).items():
        t_acc = tier.get("accuracy")
        if t_acc is None or t_acc.get("stratified_rmse"):
            continue
        sub = root / dir_map.get(tier_id, tier_id) / "cab_benchmark_result.json"
        if not sub.exists():
            continue
        with open(sub, encoding="utf-8") as fp:
            sub_data = json.load(fp)
        sub_acc = sub_data.get("dimension_1_accuracy") or {}
        if sub_acc.get("stratified_rmse"):
            t_acc["stratified_rmse"] = sub_acc["stratified_rmse"]
            t_acc["stratified_counts"] = sub_acc.get("stratified_counts") or {}
            if sub_acc.get("domain_profile"):
                t_acc["domain_profile"] = sub_acc["domain_profile"]


def _append_multitier_profiles(text: str, mdata: dict, algo_name: str) -> tuple[str, bool]:
    """Insert a per-tier profile block after each tier's detail section.

    Returns ``(new_text, inserted_any)``.
    """
    tiers = mdata.get("tiers") or {}
    if not tiers:
        return text, False

    lines = text.split("\n")
    out: list[str] = []

    # Map tier_id -> rendered profile block.
    renders: dict[str, list[str]] = {}
    for tier_id, tier in tiers.items():
        t_acc = tier.get("accuracy") or {}
        rmse = t_acc.get("stratified_rmse") or {}
        counts = t_acc.get("stratified_counts") or {}
        if not rmse:
            continue
        prof = profile_domains(rmse, pattern_counts=counts or None)
        renders[tier_id] = _render_domain_profile(prof, algo_name=algo_name)

    if not renders:
        return text, False

    tier_order = ["all", "number", "greek", "inhuman"]
    current_tier: str | None = None
    inserted = False

    for line in lines:
        out.append(line)
        # Detect tier detail headings: "### 2.N <tier_name>"
        m = re.match(r"^### 2\.(\d+) ", line.strip())
        if m:
            idx = int(m.group(1)) - 1
            if 0 <= idx < len(tier_order):
                current_tier = tier_order[idx]
        # After the tier's last bullet line, inject the profile block.
        if current_tier and line.startswith("- **计算速度**"):
            block = renders.get(current_tier)
            if block:
                out.append("")
                out.extend(block)
                inserted = True
            current_tier = None

    return "\n".join(out), inserted


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("dirs", nargs="*", help="Result directories (default: results_*)")
    ap.add_argument("--dry-run", action="store_true", help="Report only, write nothing")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    repo = Path(__file__).resolve().parent.parent
    roots = [Path(d) for d in args.dirs] if args.dirs else sorted(repo.glob("results_*"))
    roots = [r for r in roots if r.is_dir()]
    if not roots:
        print("No result directories found.")
        return 1

    totals = {"json": 0, "md": 0, "skipped": 0}
    for root in roots:
        print(f"{'[dry-run] ' if args.dry_run else ''}{root}")
        res = _patch_dir(root, args.dry_run, args.verbose)
        for k in totals:
            totals[k] += res[k]
        print(f"  -> json={res['json']} md={res['md']} skipped={res['skipped']}")

    print()
    print(f"TOTAL: json={totals['json']} md={totals['md']} skipped={totals['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
