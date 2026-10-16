#!/usr/bin/env python3
"""Annotate uuzs_bench.csv: replace column 1 (old bid) with each row's file
path relative to the samples/4k directory, then report CSV <-> file matching.

Matching priority per row:
  1. bid  (BeatmapID index built from file headers, when present)
  2. <pattern>/<name>.osu in the pattern folder (case-insensitive folder fallback)
  3. Global exact stem match
  4. Global case-insensitive stem match
  5. Unique substring fallback

Only the segment before the first comma of each line is touched; everything
else (including CRLF line endings) is preserved byte-for-byte.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples/4k"
CSVF = SAMPLES / "uuzs_bench.csv"


def build_index():
    files = sorted(SAMPLES.rglob("*.osu"))
    by_stem: dict = {}
    by_stem_ci: dict = {}
    bid_map: dict = {}
    for f in files:
        by_stem.setdefault(f.stem, []).append(f)
        by_stem_ci.setdefault(f.stem.lower(), []).append(f)
        try:
            with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                for _ in range(60):
                    line = fp.readline()
                    if not line:
                        break
                    if line.startswith("BeatmapID:"):
                        b = line.split(":", 1)[1].strip()
                        if b and b != "0":
                            bid_map.setdefault(b, []).append(f)
                        break
        except OSError:
            pass
    return files, by_stem, by_stem_ci, bid_map


def resolve(name, bid, pattern, files, by_stem, by_stem_ci, bid_map):
    pat = (pattern or "").strip()

    # 1) bid
    if bid and bid in bid_map:
        cands = bid_map[bid]
        if len(cands) == 1:
            return cands[0], "bid"
        for c in cands:  # disambiguate via pattern folder + name
            if c.parent.name.lower() == pat.lower() and c.stem == name:
                return c, "bid+pattern"
        for c in cands:
            if c.stem == name:
                return c, "bid+name"
        return None, "bid-ambiguous"

    # 2) pattern folder exact variants
    folder = SAMPLES / pat
    if not folder.exists():
        for d in SAMPLES.iterdir():
            if d.is_dir() and d.name.lower() == pat.lower():
                folder = d
                break
    if folder.is_dir():
        for cand in [folder / f"{name}.osu", folder / name, folder / f"{name} .osu"]:
            if cand.is_file():
                return cand, "pattern"

    # 3) global exact stem
    if name in by_stem:
        c = by_stem[name]
        return (c[0], "exact") if len(c) == 1 else (None, "exact-ambiguous")

    # 4) case-insensitive global
    c = by_stem_ci.get(name.lower())
    if c and len(c) == 1:
        return c[0], "ci"

    # 5) unique substring fallback
    hits = [f for f in files if name.lower() in f.stem.lower()]
    if len(hits) == 1:
        return hits[0], "substr"
    if len(hits) > 1:
        return None, "substr-ambiguous"
    return None, "unmatched"


def main() -> None:
    text = CSVF.open("r", encoding="utf-8", newline="").read()
    if '"' in text:
        print("!! CSV 含引号字符，请人工处理后重跑（脚本不支持引号字段）")
        sys.exit(1)

    # Idempotence guard: refuse to run on an already-annotated CSV. Re-running
    # would treat the existing `path` column as the old bid column and blank
    # out rows that can only be resolved via bid (e.g. short-name rows).
    first_cell = text.split("\n", 1)[0].lstrip("\ufeff").split(",", 1)[0].strip().lower()
    if first_cell == "path":
        print("!! 表头第一列已是 `path` —— 该 CSV 已完成路径标注，拒绝重复运行。")
        print("!! 再次运行会覆盖现有路径列；如确需重标注，请人工调整本守卫后再执行。")
        sys.exit(1)

    lines = text.split("\n")
    files, by_stem, by_stem_ci, bid_map = build_index()

    stats: dict = {}
    unmatched = []          # (line_no, name)
    ambiguous = []          # (line_no, name, tag)
    ref_count: dict = {}    # path -> [line_no]
    new_lines = []
    expected_empty = 0

    for idx, raw in enumerate(lines):
        if raw == "":
            continue
        has_cr = raw.endswith("\r")
        core = raw[:-1] if has_cr else raw

        if idx == 0:  # header
            head = core.split(",", 1)
            new_lines.append("path," + head[1] + ("\r" if has_cr else ""))
            continue

        seg = core.split(",")
        bid_col, name = seg[0].strip(), seg[1].strip()
        pattern = seg[2].strip() if len(seg) > 2 else ""
        if len(seg) > 4 and seg[4].strip() == "":
            expected_empty += 1

        path, tag = resolve(name, bid_col, pattern, files, by_stem, by_stem_ci, bid_map)
        stats[tag] = stats.get(tag, 0) + 1

        if path is None:
            if tag in ("unmatched", "bid-ambiguous", "exact-ambiguous", "substr-ambiguous"):
                if "ambiguous" in tag:
                    ambiguous.append((idx + 1, name, tag))
                else:
                    unmatched.append((idx + 1, name))
            rel = ""
        else:
            rel = str(path.relative_to(SAMPLES))
            ref_count.setdefault(rel, []).append(idx + 1)

        rest = core[core.index(",") + 1:] if "," in core else ""
        new_lines.append(f"{rel},{rest}" + ("\r" if has_cr else ""))

    # ---- self check: everything after the first comma must be unchanged ----
    for old_raw, new_raw in zip((r for r in lines if r != ""), new_lines):
        old_core = old_raw[:-1] if old_raw.endswith("\r") else old_raw
        new_core = new_raw[:-1] if new_raw.endswith("\r") else new_raw
        o_rest = old_core[old_core.index(",") + 1:] if "," in old_core else ""
        n_rest = new_core[new_core.index(",") + 1:] if "," in new_core else ""
        assert o_rest == n_rest, f"rest mismatch at: {old_core[:60]}"

    CSVF.write_text("\n".join(new_lines) + "\n", encoding="utf-8", newline="")

    # ---- report ----
    total = len(new_lines) - 1
    matched = total - len(unmatched) - len(ambiguous)
    print(f"=== 处理完成: {total} 数据行 | 已写入路径 {matched} | 未匹配 {len(unmatched)} | 歧义 {len(ambiguous)} ===")
    print("匹配来源分布:", dict(sorted(stats.items(), key=lambda kv: -kv[1])))
    if expected_empty:
        print(f"注意: 有 {expected_empty} 行的 expected 为空（加载时会被跳过）")

    if unmatched:
        print(f"\n--- 未匹配行（{len(unmatched)}）前 40 ---")
        for no, nm in unmatched[:40]:
            print(f"  行{no}: {nm[:80]}")
    if ambiguous:
        print(f"\n--- 歧义行（{len(ambiguous)}）前 20 ---")
        for no, nm, tag in ambiguous[:20]:
            print(f"  行{no} [{tag}]: {nm[:80]}")

    covered = set(ref_count.keys())
    all_files = {str(f.relative_to(SAMPLES)) for f in files}
    uncovered = sorted(all_files - covered)
    print(f"\n--- 文件覆盖: samples/4k 共 {len(all_files)} 个 .osu | CSV 引用 {len(covered)} 个 | 未被引用 {len(uncovered)} 个 ---")
    for u in uncovered[:40]:
        print(f"  (未引用) {u}")

    dup = {k: v for k, v in ref_count.items() if len(v) > 1}
    print(f"\n--- 重复引用（同一文件被多行引用）: {len(dup)} ---")
    for k, v in list(dup.items())[:20]:
        print(f"  {k}  <- 行 {v}")


if __name__ == "__main__":
    main()
