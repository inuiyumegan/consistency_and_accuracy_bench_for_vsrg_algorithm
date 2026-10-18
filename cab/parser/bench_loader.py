"""Benchmark dataset loader and sample registry for CAB-VA."""

from __future__ import annotations

import csv
import glob
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

from cab.parser.osu_parser import Beatmap


@dataclass(slots=True)
class BenchmarkSample:
    bid: Optional[int]
    name: str
    pattern: str
    sub_pattern: str
    expected: float
    file_path: Path
    _beatmap: Optional[Beatmap] = None

    @property
    def is_course(self) -> bool:
        return (
            self.pattern.lower() == "course"
            or "samples/4k/course" in str(self.file_path).replace("\\", "/").lower()
        )

    @property
    def is_ln(self) -> bool:
        return (
            self.pattern.lower() == "ln"
            or "samples/4k/ln" in str(self.file_path).replace("\\", "/").lower()
        )

    def load_beatmap(self) -> Beatmap:
        if self._beatmap is None:
            self._beatmap = Beatmap.parse(self.file_path)
        return self._beatmap

    @property
    def duration_seconds(self) -> float:
        return self.load_beatmap().duration_seconds


class BenchmarkDataset:
    def __init__(self, samples: Sequence[BenchmarkSample]):
        self.samples: List[BenchmarkSample] = list(samples)

    def __len__(self) -> int:
        return len(self.samples)

    def __iter__(self):
        return iter(self.samples)

    def __getitem__(self, idx: int) -> BenchmarkSample:
        return self.samples[idx]

    def filter(
        self,
        patterns: Optional[Sequence[str]] = None,
        exclude_patterns: Optional[Sequence[str]] = None,
        min_expected: Optional[float] = None,
        max_expected: Optional[float] = None,
        name_filter: Optional[str] = None,
        min_inclusive: bool = True,
        max_inclusive: bool = True,
    ) -> BenchmarkDataset:
        """Filter dataset by pattern whitelist/blacklist, difficulty bounds, and name."""
        filtered = self.samples
        if patterns:
            pattern_set = {p.strip().lower() for p in patterns}
            filtered = [s for s in filtered if s.pattern.lower() in pattern_set]

        if exclude_patterns:
            exclude_set = {p.strip().lower() for p in exclude_patterns}
            filtered = [s for s in filtered if s.pattern.lower() not in exclude_set]

        if min_expected is not None:
            min_val = float(min_expected)
            if min_inclusive:
                filtered = [s for s in filtered if s.expected >= min_val]
            else:
                filtered = [s for s in filtered if s.expected > min_val]

        if max_expected is not None:
            max_val = float(max_expected)
            if max_inclusive:
                filtered = [s for s in filtered if s.expected <= max_val]
            else:
                filtered = [s for s in filtered if s.expected < max_val]

        if name_filter:
            kw = name_filter.strip().lower()
            filtered = [s for s in filtered if kw in s.name.lower()]

        return BenchmarkDataset(filtered)

    def get_stats_summary(self) -> Dict[str, Any]:
        """Return dataset size, pattern counts, and difficulty bounds."""
        if not self.samples:
            return {
                "total": 0,
                "patterns": {},
                "min_expected": 0.0,
                "max_expected": 0.0,
            }

        patterns_count: Dict[str, int] = {}
        for s in self.samples:
            p = s.pattern.lower()
            patterns_count[p] = patterns_count.get(p, 0) + 1

        exp_vals = [s.expected for s in self.samples]
        return {
            "total": len(self.samples),
            "patterns": patterns_count,
            "min_expected": float(min(exp_vals)),
            "max_expected": float(max(exp_vals)),
        }

    def filter_by_patterns(self, patterns: Sequence[str]) -> BenchmarkDataset:
        pattern_set = {p.lower() for p in patterns}
        return BenchmarkDataset([s for s in self.samples if s.pattern.lower() in pattern_set])

    def get_accuracy_samples(self) -> List[BenchmarkSample]:
        """All benchmark samples, including course/dan marathons."""
        return list(self.samples)

    def get_perturbation_samples(self) -> List[BenchmarkSample]:
        """Samples for rate, jitter, continuity, and short-LN tests.
        
        STRICTLY EXCLUDES /samples/4k/course and /samples/4k/ln beatmaps.
        Only single-song rice patterns (jack, speed, stamina, tech) are evaluated.
        """
        return [s for s in self.samples if not s.is_course and not s.is_ln]

    def group_by_pattern(self) -> Dict[str, List[BenchmarkSample]]:
        grouped: Dict[str, List[BenchmarkSample]] = {}
        for s in self.samples:
            grouped.setdefault(s.pattern.lower(), []).append(s)
        return grouped

    def group_by_tier(self) -> Dict[float, List[BenchmarkSample]]:
        grouped: Dict[float, List[BenchmarkSample]] = {}
        for s in self.samples:
            grouped.setdefault(round(s.expected, 2), []).append(s)
        return dict(sorted(grouped.items()))

    @classmethod
    def load_from_csv(
        cls,
        csv_path: Union[str, Path] = "samples/4k/uuzs_bench.csv",
        samples_root: Optional[Union[str, Path]] = None,
    ) -> BenchmarkDataset:
        """Load benchmark dataset from CSV with robust beatmap path resolution."""
        csv_file = Path(csv_path).resolve()
        if not csv_file.exists():
            raise FileNotFoundError(f"Benchmark CSV not found: {csv_file}")

        # Guard: reject binary spreadsheets masquerading as CSV (e.g. an .xlsx
        # saved under a .csv name). A plain-text CSV never starts with the ZIP
        # magic "PK" / OLE "D0 CF 11 E0" and never contains NUL bytes.
        _head = csv_file.open("rb").read(4096)
        if _head.startswith(b"PK") or _head.startswith(b"\xd0\xcf\x11\xe0") or b"\x00" in _head:
            raise ValueError(
                f"Benchmark file is not a plain-text CSV (binary/spreadsheet content detected): {csv_file}\n"
                "Hint: re-export it as a UTF-8 CSV file and retry."
            )

        if samples_root is None:
            samples_dir = csv_file.parent
        else:
            samples_dir = Path(samples_root).resolve()

        # Build index of existing .osu files in samples directory
        all_osu_files = list(samples_dir.glob("**/*.osu"))
        bid_map: Dict[str, Path] = {}

        # Quick index of BeatmapIDs
        for f in all_osu_files:
            try:
                with open(f, "r", encoding="utf-8", errors="ignore") as fp:
                    for _ in range(50):
                        line = fp.readline()
                        if not line:
                            break
                        if line.startswith("BeatmapID:"):
                            b = line.split(":", 1)[1].strip()
                            if b and b != "0":
                                bid_map[b] = f
                            break
            except Exception:
                pass

        samples: List[BenchmarkSample] = []
        with open(csv_file, "r", encoding="utf-8-sig", errors="replace") as fp:
            reader = csv.DictReader(fp)
            for row in reader:
                name = row.get("name", "").strip()
                pattern = row.get("pattern", "").strip()
                sub_pattern = row.get("subPattern", "").strip()
                expected_str = row.get("expected", "").strip()
                if not expected_str or not name:
                    continue

                try:
                    expected = float(expected_str)
                except ValueError:
                    continue

                bid_val: Optional[int] = None
                raw_bid = row.get("bid", "").strip()
                if raw_bid:
                    try:
                        bid_val = int(float(raw_bid))
                    except ValueError:
                        pass

                # Resolve file
                matched_path: Optional[Path] = None

                # 0. Match by explicit relative path column (preferred, deterministic).
                #    Falls through to bid/name heuristics when absent or file missing.
                raw_path = (row.get("path") or "").strip()
                if raw_path:
                    cand = samples_dir / raw_path
                    if cand.is_file():
                        matched_path = cand

                # 1. Match by bid
                if not matched_path and bid_val and str(bid_val) in bid_map:
                    matched_path = bid_map[str(bid_val)]

                # 2. Match exact name variants in pattern folder
                if not matched_path:
                    pattern_folder = samples_dir / pattern
                    if not pattern_folder.exists():
                        # Case-insensitive folder fallback (e.g. CSV 'Unsigned' -> 'unsigned')
                        for d in samples_dir.iterdir():
                            if d.is_dir() and d.name.lower() == pattern.lower():
                                pattern_folder = d
                                break
                    for cand in [
                        pattern_folder / f"{name}.osu",
                        pattern_folder / name,
                        pattern_folder / f"{name} .osu",
                    ]:
                        if cand.exists():
                            matched_path = cand
                            break

                # 3. Match by name in pattern folder
                if not matched_path and pattern_folder.exists():
                    pattern_files = list(pattern_folder.glob("*.osu"))
                    for pf in pattern_files:
                        bname = pf.name
                        if name.lower() in bname.lower() or bname.lower().startswith(name.lower()[:15]):
                            matched_path = pf
                            break

                # 4. Global match
                if not matched_path:
                    for pf in all_osu_files:
                        if name.lower() in pf.name.lower():
                            matched_path = pf
                            break

                if matched_path and matched_path.exists():
                    samples.append(
                        BenchmarkSample(
                            bid=bid_val,
                            name=name,
                            pattern=pattern,
                            sub_pattern=sub_pattern,
                            expected=expected,
                            file_path=matched_path,
                        )
                    )

        return cls(samples)
