#!/usr/bin/env python3
"""Recompute CAB-VA scores after an ACC-metric change, without re-running algorithms.

Background
----------
The ACC dimension aggregates per-pattern RMSEs into a *balanced stratified
RMSE* using the fixed ``PATTERN_WEIGHTS`` table.  Two successive changes to that
aggregation (unclassified patterns were implicitly weighted ``0.1``; ``unsigned``
is now pinned at the ``course`` level) alter every ACC sub-score downstream.

Because only the *scoring function* changed (not the measurement), every
affected number can be recomputed straight from the stored JSON.  This tool:

  1. Walks every ``results_*/**/cab_benchmark_result.json`` (single-tier files),
     re-derives the balanced stratified RMSE from the raw per-pattern
     ``stratified_rmse`` map, then rewrites the ACC sub-score and the total
     inside ``score_summary`` (and mirrors the new value back into
     ``dimension_1_accuracy``).
  2. Refreshes the per-tier entries inside each ``cab_multi_tier_summary.json``
     from the corresponding tier sub-directory JSONs.

It is idempotent: running it twice yields identical output.  Use ``--dry-run``
to preview the diffs without touching any file.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

# Allow running as a plain script from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cab.metrics.scoring import (  # noqa: E402
    ACC_RMSE_BASELINE,
    ACC_RMSE_DECAY,
    WEIGHT_ACCURACY,
    WEIGHT_ACCURACY_SUBTIER,
    WEIGHT_CONTINUITY,
    WEIGHT_INVERSION,
    WEIGHT_JITTER,
    WEIGHT_JITTER_SUBTIER,
    WEIGHT_SHORT_LN,
    WEIGHT_SHORT_LN_SUBTIER,
    WEIGHT_SPEED,
    WEIGHT_SPEED_SUBTIER,
)

# Mapping from the JSON dimension labels to the weight constants.
_ALL_WEIGHTS: Dict[str, float] = {
    "Ground-Truth Accuracy": WEIGHT_ACCURACY,
    "Anti-Rate-Inversion": WEIGHT_INVERSION,
    "Anti-Short LN": WEIGHT_SHORT_LN,
    "Local Continuity": WEIGHT_CONTINUITY,
    "Computation Speed": WEIGHT_SPEED,
    "Anti-Jitter": WEIGHT_JITTER,
}
_SUBTIER_WEIGHTS: Dict[str, float] = {
    "Ground-Truth Accuracy": WEIGHT_ACCURACY_SUBTIER,
    "Anti-Short LN": WEIGHT_SHORT_LN_SUBTIER,
    "Computation Speed": WEIGHT_SPEED_SUBTIER,
    "Anti-Jitter": WEIGHT_JITTER_SUBTIER,
}

# Per-pattern weights used to aggregate the balanced stratified RMSE. Mirrors
# ``cab.metrics.accuracy`` — keep the two in sync. ``unsigned`` is pinned at the
# ``course`` level so a 12-map catch-all bucket cannot outweigh a proper pattern.
PATTERN_WEIGHTS: Dict[str, float] = {
    "jack": 0.20,
    "speed": 0.20,
    "stamina": 0.20,
    "tech": 0.20,
    "ln": 0.15,
    "course": 0.05,
    "unsigned": 0.05,
}
DEFAULT_PATTERN_WEIGHT: float = 0.05


def recompute_weighted_stratified_rmse(
    stratified_rmse: Dict[str, float],
    stratified_counts: Optional[Dict[str, int]] = None,
) -> Optional[float]:
    """Re-derive the balanced stratified RMSE from per-pattern RMSE values.

    Mirrors ``cab.metrics.accuracy``: a **weighted average of per-pattern
    RMSEs** (weights sum to 1 after normalisation), *not* a weighted mean of
    squared errors. ``stratified_counts`` is accepted for signature symmetry
    but is not needed by this aggregation.
    """
    if not stratified_rmse:
        return None
    num = 0.0
    den = 0.0
    for pat, rmse in stratified_rmse.items():
        if rmse is None:
            continue
        w = PATTERN_WEIGHTS.get(pat, DEFAULT_PATTERN_WEIGHT)
        num += w * float(rmse)
        den += w
    if den <= 0:
        return None
    return num / den


def recompute_accuracy_score(weighted_rmse: float, rho: float) -> float:
    """Mirror the ACC sub-score formula from ``cab.metrics.scoring``."""
    rho_score = 60.0 * max(0.0, rho) ** 2
    rmse_score = 40.0 * math.exp(
        -ACC_RMSE_DECAY * max(0.0, weighted_rmse - ACC_RMSE_BASELINE)
    )
    return max(0.0, min(100.0, rho_score + rmse_score))


def _weighted_total(
    dimension_scores: Dict[str, Optional[float]],
    is_subtier: bool,
) -> Tuple[float, Dict[str, float]]:
    weights = _SUBTIER_WEIGHTS if is_subtier else _ALL_WEIGHTS
    total = 0.0
    for name, w in weights.items():
        score = dimension_scores.get(name)
        total += w * (score if score is not None else 0.0)
    return round(total, 2), dict(weights)


def recompute_score_summary(score_summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Return an updated ``score_summary`` dict, or None if nothing changed.

    The ACC sub-score is re-derived from the *balanced stratified RMSE* which,
    in turn, is recomputed from the raw per-pattern ``stratified_rmse`` map using
    the current ``PATTERN_WEIGHTS``.  This is what makes the ``unsigned``
    re-weighting take effect: the old stored ``weighted_stratified_rmse`` was
    produced with the buggy fallback weight and must not be trusted.
    """
    if score_summary.get("is_out_of_domain"):
        return None

    dims = dict(score_summary.get("dimensions") or {})
    # Copy, never replace: the stored ``accuracy`` block also carries
    # ``calibrated_rmse`` / ``domain_profile`` / ... which we must preserve.
    accuracy = dict(score_summary.get("accuracy") or {})

    stratified = accuracy.get("stratified_rmse")
    if stratified:
        weighted_rmse = recompute_weighted_stratified_rmse(
            stratified, accuracy.get("stratified_counts")
        )
        if weighted_rmse is None:
            weighted_rmse = accuracy.get("weighted_stratified_rmse")
    else:
        weighted_rmse = accuracy.get("weighted_stratified_rmse")
    rho = accuracy.get("spearman_rho")
    if weighted_rmse is None or rho is None:
        return None

    is_subtier = bool(score_summary.get("is_subtier"))
    new_acc = round(recompute_accuracy_score(float(weighted_rmse), float(rho)), 2)

    old_acc = dims.get("Ground-Truth Accuracy")
    old_weighted = accuracy.get("weighted_stratified_rmse")
    if old_acc == new_acc and old_weighted == round(float(weighted_rmse), 6):
        return None

    old_total = score_summary.get("total_score")
    dims["Ground-Truth Accuracy"] = new_acc
    new_total, weights = _weighted_total(dims, is_subtier)

    accuracy["weighted_stratified_rmse"] = round(float(weighted_rmse), 6)

    updated = dict(score_summary)
    updated["dimensions"] = dims
    updated["accuracy"] = accuracy
    updated["total_score"] = new_total
    updated["weights"] = weights
    updated["_previous"] = {
        "total_score": old_total,
        "accuracy": old_acc,
        "weighted_rmse": old_weighted,
    }
    return updated


def _process_single_tier_json(path: Path, dry_run: bool) -> Optional[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    ss = data.get("score_summary")
    if not ss:
        return None

    # ``dimension_1_accuracy`` is the authoritative measurement block: it holds
    # the raw per-pattern ``stratified_rmse`` map we need to re-aggregate.  The
    # compact ``score_summary.accuracy`` block may omit it, so seed from there.
    # We *merge* into the existing block to keep ``domain_profile`` and friends.
    src_acc = data.get("dimension_1_accuracy") or {}
    acc_block = dict(ss.get("accuracy") or {})
    for key in ("stratified_rmse", "stratified_counts", "spearman_rho"):
        if acc_block.get(key) is None and src_acc.get(key) is not None:
            acc_block[key] = src_acc[key]
    if acc_block.get("weighted_stratified_rmse") is None:
        acc_block["weighted_stratified_rmse"] = src_acc.get("weighted_stratified_rmse")
    ss["accuracy"] = acc_block

    updated = recompute_score_summary(ss)
    if updated is None:
        return None

    prev = updated.pop("_previous")

    # Keep the authoritative measurement block in sync with the re-derived value.
    new_weighted = updated["accuracy"]["weighted_stratified_rmse"]
    dim1 = data.get("dimension_1_accuracy")
    if isinstance(dim1, dict):
        dim1["weighted_stratified_rmse"] = new_weighted

    line = (
        f"{path}: total {prev['total_score']} -> {updated['total_score']} "
        f"(wRMSE {prev['weighted_rmse']} -> {new_weighted}, "
        f"ACC {prev['accuracy']} -> {updated['dimensions']['Ground-Truth Accuracy']})"
    )
    if not dry_run:
        data["score_summary"] = updated
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return line


def _process_multitier_json(path: Path, dry_run: bool) -> list[str]:
    """Refresh each tier entry in a multi-tier summary from its sub-dir JSON.

    Runs *after* the single-tier pass, so the sub-dir JSON already carries the
    re-weighted values; we simply mirror them into the summary.  The sub-dir
    ``score_summary`` is re-derived here too, so a standalone run over a
    directory whose children were not yet touched still converges.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    tiers = data.get("tiers")
    if not isinstance(tiers, dict):
        return []

    base = path.parent
    subdir_map = {
        "all": "all",
        "number": "number_dan",
        "greek": "greek_dan",
        "inhuman": "inhuman_dan",
    }
    changes: list[str] = []
    for tier_key, entry in tiers.items():
        sub = subdir_map.get(tier_key)
        if not sub:
            continue
        sub_json = base / sub / "cab_benchmark_result.json"
        if not sub_json.is_file():
            continue
        sub_data = json.loads(sub_json.read_text(encoding="utf-8"))
        sub_ss = sub_data.get("score_summary") or {}

        # Re-derive from the raw per-pattern map in the sub-dir measurement block.
        # Merge (never replace) so ``domain_profile`` etc. survive.
        src_acc = sub_data.get("dimension_1_accuracy") or {}
        acc_block = dict(sub_ss.get("accuracy") or {})
        for key in ("stratified_rmse", "stratified_counts", "spearman_rho"):
            if acc_block.get(key) is None and src_acc.get(key) is not None:
                acc_block[key] = src_acc[key]
        if acc_block.get("weighted_stratified_rmse") is None:
            acc_block["weighted_stratified_rmse"] = src_acc.get(
                "weighted_stratified_rmse"
            )
        sub_ss["accuracy"] = acc_block

        refreshed = recompute_score_summary(sub_ss)
        if refreshed is not None:
            refreshed.pop("_previous", None)
            sub_ss = refreshed
            if not dry_run:
                sub_data["score_summary"] = refreshed
                dim1 = sub_data.get("dimension_1_accuracy")
                if isinstance(dim1, dict):
                    dim1["weighted_stratified_rmse"] = refreshed["accuracy"][
                        "weighted_stratified_rmse"
                    ]
                sub_json.write_text(
                    json.dumps(sub_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

        new_total = sub_ss.get("total_score")
        if new_total is None:
            continue

        # Mirror only the balanced-RMSE scalar into the tier entry's accuracy
        # block; every other field there (domain_profile, calibrated_rmse, ...)
        # must be left untouched.
        entry_acc = dict(entry.get("accuracy") or {})
        new_weighted = (sub_ss.get("accuracy") or {}).get("weighted_stratified_rmse")
        old_weighted = entry_acc.get("weighted_stratified_rmse")
        old_total = entry.get("total_score")

        if old_total != new_total or old_weighted != new_weighted:
            entry["total_score"] = new_total
            entry["dimension_scores"] = dict(sub_ss.get("dimensions") or {})
            entry_acc["weighted_stratified_rmse"] = new_weighted
            entry["accuracy"] = entry_acc
            changes.append(
                f"{path} [{tier_key}]: total {old_total} -> {new_total} "
                f"(wRMSE {old_weighted} -> {new_weighted})"
            )

    if changes and not dry_run:
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="preview changes only")
    parser.add_argument(
        "roots",
        nargs="*",
        default=None,
        help="result directories to process (default: every results_*/ in cwd)",
    )
    args = parser.parse_args()

    cwd = Path.cwd()
    roots = (
        [Path(p) for p in args.roots]
        if args.roots
        else sorted(p for p in cwd.glob("results_*") if p.is_dir())
    )
    if not roots:
        print("No result directories found.")
        return 1

    singles = 0
    multis = 0
    for root in roots:
        for json_path in sorted(root.rglob("cab_benchmark_result.json")):
            if _process_single_tier_json(json_path, args.dry_run):
                singles += 1
        for json_path in sorted(root.rglob("cab_multi_tier_summary.json")):
            for line in _process_multitier_json(json_path, args.dry_run):
                print(line)
                multis += 1

    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"\n[{mode}] updated {singles} single-tier file(s), {multis} multi-tier entry/entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
