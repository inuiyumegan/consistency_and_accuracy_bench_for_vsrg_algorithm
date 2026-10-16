#!/usr/bin/env python3
"""Split the three REFORM INTRO courses (INTRO-1st/2nd/3rd) into 12 singles.

For each course:
  - Uses the three [Events] break periods as track boundaries (4 tracks each).
  - Extracts hit objects & timing points of each track.
  - Shifts each track's timeline so its first object starts at the same
    lead-in offset as the course's first track.
  - Writes .osu files into samples/4k/unsigned/ with metadata rebuilt from the
    official single names (Artist - Title (Creator) [Version]).

Expected (ng) mapping: INTRO-1st tracks -> -2, INTRO-2nd -> -1, INTRO-3rd -> 0
(matching the course-level anchors already present in uuzs_bench.csv).
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COURSE_DIR = ROOT / "samples/4k/course"
OUT_DIR = ROOT / "samples/4k/unsigned"

NAME_RE = re.compile(
    r"^(?P<artist>.+?) - (?P<title>.+?) \((?P<creator>.+?)\) \[(?P<version>.+)\]$"
)

PLAN = [
    ("INTRO-1st", -2, [
        "Nanamori-chu * Goraku-bu - Yuriyurarararayuruyuri Daijiken (Lokovodo) [EZ]",
        "Camellia - Ultimate Ascension (Fresh Chicken) [BASIC]",
        "Hommarju - Sounds of Summer (Shana Lesus) [Beginner]",
        "Suzuki Konomi - Redo ( R e m u r i n g ) [Easy]",
    ]),
    ("INTRO-2nd", -1, [
        "Banshi - Kotonoha (erlinadewi-) [Normal]",
        "Jeff Williams - This Will Be the Day (arcwinolivirus) [4K Normal]",
        "O2i3 - Belly Flopper (Ilham) [NOVICE]",
        "bbn - Identic Conflict (S a k u r a) [Normal]",
    ]),
    ("INTRO-3rd", 0, [
        "Avenged Sevenfold - A Little Piece of Heaven (Renard) [MGD3]",
        "TAG - Romancing Layer (AmiShici) [Avalon\u2019s MEDIUM]",
        "Memme - Chinese Restaurant (ajeemaniz) [Advanced Saucy Meatball]",
        "S-C-U feat. Qrispy Joybox - anemone (Julie) [4K Hyper]",
    ]),
]


def find_course_file(tag: str) -> Path:
    matches = [p for p in COURSE_DIR.glob("*.osu") if tag in p.name]
    assert len(matches) == 1, f"{tag}: ambiguity {matches}"
    return matches[0]


def obj_time(line: str) -> int:
    return int(float(line.split(",")[2]))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    total = 0
    for tag, expected, singles in PLAN:
        course_file = find_course_file(tag)
        lines = course_file.read_text(encoding="utf-8", errors="replace").splitlines()

        breaks, notes, timings = [], [], []
        section = None
        for ln in lines:
            s = ln.strip()
            if s.startswith("["):
                section = s
                continue
            if not s or s.startswith("//"):
                continue
            if section == "[Events]" and s.startswith("2,"):
                p = s.split(",")
                breaks.append((int(p[1]), int(p[2])))
            elif section == "[TimingPoints]":
                timings.append(int(float(s.split(",")[0])))
            elif section == "[HitObjects]":
                notes.append(obj_time(s))
        assert len(breaks) == 3, f"{tag}: breaks={breaks}"
        notes.sort()

        seg_bounds = [0] + [b[1] for b in breaks]
        cuts = [b[0] for b in breaks] + [10 ** 9]
        base = min(min(notes), min(timings))

        def belongs(t: int, seg_idx: int) -> bool:
            return seg_bounds[seg_idx] <= t < cuts[seg_idx]

        print(f"\n### {tag} (expected ng {expected}) | {course_file.name}")
        for seg_idx, full_name in enumerate(singles):
            m = NAME_RE.match(full_name)
            assert m, f"name parse failed: {full_name}"
            artist = m.group("artist")
            title = m.group("title")
            creator = m.group("creator").strip()
            version = m.group("version")

            seg_notes = [t for t in notes if belongs(t, seg_idx)]
            seg_timings = [t for t in timings if belongs(t, seg_idx)]
            assert seg_notes and seg_timings, f"{tag} seg{seg_idx + 1}: empty"
            shift = min(seg_notes + seg_timings) - base

            out_lines = []
            section = None
            for ln in lines:
                s = ln.strip()
                if s.startswith("["):
                    section = s
                    out_lines.append(ln)
                    continue
                if section == "[Events]" and s.startswith("2,"):
                    continue  # drop break periods
                if section == "[Metadata]" and ":" in s:
                    key = s.split(":", 1)[0]
                    if key in ("Title", "TitleUnicode"):
                        out_lines.append(f"{key}:{title}")
                        continue
                    if key in ("Artist", "ArtistUnicode"):
                        out_lines.append(f"{key}:{artist}")
                        continue
                    if key == "Creator":
                        out_lines.append(f"Creator:{creator}")
                        continue
                    if key == "Version":
                        out_lines.append(f"Version:{version}")
                        continue
                    if key in ("BeatmapID", "BeatmapSetID"):
                        out_lines.append(f"{key}:0")
                        continue
                    out_lines.append(ln)
                    continue
                if section == "[TimingPoints]" and s and not s.startswith("//"):
                    t = int(float(s.split(",")[0]))
                    if not belongs(t, seg_idx):
                        continue
                    parts = s.split(",")
                    parts[0] = str(t - shift)
                    out_lines.append(",".join(parts))
                    continue
                if section == "[HitObjects]" and s:
                    t = obj_time(s)
                    if not belongs(t, seg_idx):
                        continue
                    parts = s.split(",")
                    parts[2] = str(t - shift)
                    if len(parts) > 5 and ":" in parts[5]:
                        sub = parts[5].split(":")
                        sub[0] = str(int(float(sub[0])) - shift)
                        parts[5] = ":".join(sub)
                    out_lines.append(",".join(parts))
                    continue
                out_lines.append(ln)

            out_path = OUT_DIR / f"{full_name}.osu"
            out_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
            total += 1
            print(f"  [ng {expected:>2}] {full_name}")
            print(f"          notes={len(seg_notes)} | timings={len(seg_timings)} | "
                  f"span={seg_notes[0] - shift}~{seg_notes[-1] - shift} ms")

    print(f"\n\u2713 共生成 {total} 个单曲文件 → {OUT_DIR}")


if __name__ == "__main__":
    main()
