#!/usr/bin/env python3
"""Normalize OverallDifficulty of benchmark maps by ng tier (uuzs_bench.csv).

Rationale: benchmark inputs must not carry stray OD values that mislead
algorithms reading `OverallDifficulty` (e.g. Star-Rating-Rebirth's hit-leniency
term, or the Sunny-family estimators). All referenced maps are normalized to a
tiered OD keyed on the CSV `expected` (ng) value.

Tier rules (分档边界值归入下方档位 — this mirrors the Mixed estimator's
RC-difficulty boundary behaviour, e.g. 10.5 -> "Reform 10 high"):

  non-LN folders:  ng <= 0.5  -> 7.0
                   ng <= 3.5  -> 7.5
                   ng <= 6.5  -> 8.0
                   ng <= 10.5 -> 8.5
                   ng <= 16.5 -> 9.0
                   else       -> 9.2
  ln folder:       ng <= 13.5 -> 8.0
                   else       -> 8.5

Only the `OverallDifficulty:` value inside [Difficulty] is touched (byte-level
replacement); every other byte of each .osu file is preserved. A manifest of
original values is written on --apply, enabling full rollback.

Usage:
  python3 scripts/normalize_sample_od.py                    # dry run (report only)
  python3 scripts/normalize_sample_od.py --apply            # write files + manifest
  python3 scripts/normalize_sample_od.py --revert MANIFEST  # restore original ODs
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples/4k"
CSVF = SAMPLES / "uuzs_bench.csv"
DEFAULT_MANIFEST = SAMPLES / "od_manifest_before_normalization.csv"

OD_RE = re.compile(rb"(?m)^OverallDifficulty:\s*([0-9.]+)")

NON_LN_BOUNDS = [0.5, 3.5, 6.5, 10.5, 16.5]
LN_BOUNDS = [13.5]


def target_od(folder: str, ng: float) -> float:
    if folder == "ln":
        return 8.0 if ng <= 13.5 else 8.5
    if ng <= 0.5:
        return 7.0
    if ng <= 3.5:
        return 7.5
    if ng <= 6.5:
        return 8.0
    if ng <= 10.5:
        return 8.5
    if ng <= 16.5:
        return 9.0
    return 9.2


def fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def load_targets():
    """Return (path -> (folder, ng, target_od), conflicts)."""
    rows = list(csv.DictReader(CSVF.open("r", encoding="utf-8", newline="")))
    tgt: dict = {}
    conflicts = []
    for r in rows:
        p = (r.get("path") or "").strip()
        if not p:
            continue
        ng = float((r.get("expected") or "").strip())
        folder = p.split("/")[0]
        t = target_od(folder, ng)
        if p in tgt:
            if tgt[p][2] != t:
                conflicts.append((p, tgt[p][2], t))
        else:
            tgt[p] = (folder, ng, t)
    return tgt, conflicts


def read_od(path: Path):
    data = path.read_bytes()
    m = OD_RE.search(data)
    return data, m, (m.group(1).decode() if m else None)


def strip_od(data: bytes) -> bytes:
    """Blank out the OD value so two versions can be compared modulo it."""
    return OD_RE.sub(b"OverallDifficulty:#", data, count=1)


def do_dry(tgt) -> None:
    from collections import Counter

    dist = Counter()
    boundary = []
    will_change = already = 0
    for rel, (folder, ng, t) in sorted(tgt.items()):
        dist[t] += 1
        bounds = LN_BOUNDS if folder == "ln" else NON_LN_BOUNDS
        for b in bounds:
            if abs(ng - b) < 1e-9:
                boundary.append((rel, ng, t))
                break
        data, m, old = read_od(SAMPLES / rel)
        if old == fmt(t):
            already += 1
        else:
            will_change += 1
    print("== dry run ==")
    print("目标 OD 分布:", {k: dist[k] for k in sorted(dist)})
    print(f"文件总数 {len(tgt)} | 需改写 {will_change} | 已是目标值 {already}")
    print(f"边界值文件（归下方档位）: {len(boundary)}")
    for rel, ng, t in boundary:
        print(f"    ng={ng:<5} -> OD {fmt(t):<4} | {rel}")


def do_apply(tgt) -> None:
    manifest = []
    changed = same = 0
    problems = []
    for rel, (folder, ng, t) in sorted(tgt.items()):
        f = SAMPLES / rel
        if not f.exists():
            problems.append(f"missing: {rel}")
            continue
        data, m, old = read_od(f)
        if m is None:
            problems.append(f"no OD line: {rel}")
            continue
        new_s = fmt(t)
        if old == new_s:
            same += 1
            continue
        new_data = data[: m.start(1)] + new_s.encode() + data[m.end(1):]
        f.write_bytes(new_data)
        re_read = f.read_bytes()
        if strip_od(re_read) != strip_od(data):
            problems.append(f"integrity check failed: {rel}")
            continue
        changed += 1
        manifest.append((rel, folder, ng, old, new_s))
    print(f"== apply: 改写 {changed} | 已是目标值 {same} | 问题 {len(problems)} ==")
    for p in problems:
        print("  !", p)
    with DEFAULT_MANIFEST.open("w", encoding="utf-8", newline="") as fp:
        w = csv.writer(fp, lineterminator="\n")
        w.writerow(["path", "folder", "ng", "old_od", "new_od"])
        w.writerows(manifest)
    print(f"manifest -> {DEFAULT_MANIFEST.relative_to(ROOT)} ({len(manifest)} rows)")


def do_revert(manifest_path: str) -> None:
    n = 0
    with open(manifest_path, encoding="utf-8", newline="") as fp:
        for row in csv.DictReader(fp):
            f = SAMPLES / row["path"]
            data, m, cur = read_od(f)
            if m is None:
                print("  ! no OD line:", row["path"])
                continue
            if cur == row["old_od"]:
                continue
            new_data = data[: m.start(1)] + row["old_od"].encode() + data[m.end(1):]
            f.write_bytes(new_data)
            n += 1
    print(f"== revert: 还原 {n} 个文件 ==")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="write files + manifest")
    ap.add_argument("--revert", metavar="MANIFEST", help="restore original ODs from manifest")
    args = ap.parse_args()

    if args.revert:
        do_revert(args.revert)
        return

    tgt, conflicts = load_targets()
    if conflicts:
        print("!! 同一文件出现不同目标 OD（需人工处理）:", conflicts)
        sys.exit(1)
    if args.apply:
        do_apply(tgt)
    else:
        do_dry(tgt)


if __name__ == "__main__":
    main()
