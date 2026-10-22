"""Benchmark dataset loader and sample registry for CAB-VA.

Loading policy:

* Every CSV row must resolve to a real file.  Rows that cannot be resolved are
  collected in :attr:`BenchmarkDataset.unresolved` and raise by default —
  a silently smaller dataset produced scores that were not comparable with
  history while looking perfectly healthy.
* ``Mode`` must be mania and ``CircleSize`` must be 4; a 7K chart mis-read as
  4K synthesises fake jacks.
* ``expected`` must be finite; duplicate paths and duplicate rows are rejected.
"""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from cab.parser.osu_parser import Beatmap


#: Columns every benchmark CSV must provide.
_REQUIRED_COLUMNS = ("path", "name", "pattern")

#: Header lines read per file while building the metadata index.  ``[Difficulty]``
#: normally starts well within this budget.
_META_SCAN_LINES = 200


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
        #: Rows that could not be resolved (populated by ``load_from_csv``).
        self.unresolved: List[Dict[str, Any]] = []
        #: CSV the dataset came from, for provenance in reports.
        self.source_csv: Optional[Path] = None

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

        out = BenchmarkDataset(filtered)
        out.unresolved = list(self.unresolved)
        out.source_csv = self.source_csv
        return out

    def get_stats_summary(self) -> Dict[str, Any]:
        """Return dataset size, pattern counts, and difficulty bounds."""
        if not self.samples:
            return {
                "total": 0,
                "patterns": {},
                "min_expected": None,
                "max_expected": None,
                "unresolved": len(self.unresolved),
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
            "unresolved": len(self.unresolved),
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

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------
    @staticmethod
    def _scan_meta(path: Path) -> Dict[str, Any]:
        """Read the header of a .osu file once: bid / Mode / CircleSize."""
        meta: Dict[str, Any] = {"bid": None, "mode": None, "circle_size": None}
        try:
            with open(path, "r", encoding="utf-8-sig", errors="replace", newline="") as fp:
                for _ in range(_META_SCAN_LINES):
                    line = fp.readline()
                    if not line:
                        break
                    stripped = line.strip()
                    if stripped.startswith("BeatmapID:"):
                        raw = stripped.split(":", 1)[1].strip()
                        if raw and raw != "0":
                            try:
                                meta["bid"] = int(raw)
                            except ValueError:
                                pass
                    elif stripped.startswith("Mode:"):
                        meta["mode"] = stripped.split(":", 1)[1].strip()
                    elif stripped.startswith("CircleSize:"):
                        raw = stripped.split(":", 1)[1].strip()
                        try:
                            meta["circle_size"] = int(float(raw))
                        except ValueError:
                            meta["circle_size"] = None
                    elif stripped.startswith("[HitObjects]"):
                        break
        except OSError:
            pass
        return meta

    @classmethod
    def _index_files(cls, files: Iterable[Path]) -> Tuple[Dict[str, Path], Dict[Path, Dict[str, Any]]]:
        bid_map: Dict[str, Path] = {}
        meta_map: Dict[Path, Dict[str, Any]] = {}
        for f in files:
            meta = cls._scan_meta(f)
            meta_map[f] = meta
            bid = meta.get("bid")
            if bid is not None:
                bid_map[str(bid)] = f
        return bid_map, meta_map

    @classmethod
    def load_from_csv(
        cls,
        csv_path: Union[str, Path] = "samples/4k/uuzs_bench.csv",
        samples_root: Optional[Union[str, Path]] = None,
        expected_column: str = "expected",
        strict: bool = True,
        require_mania_4k: bool = True,
    ) -> BenchmarkDataset:
        """Load benchmark dataset from CSV with strict beatmap path resolution.

        Args:
            csv_path: benchmark manifest.
            samples_root: root for relative ``path`` values (defaults to the
                CSV's directory).
            expected_column: truth-value column name.  ``signicials_bench.csv``
                uses ``expectedinsignicial`` and must never be mixed with the
                ND-scaled ``uuzs_bench.csv``.
            strict: raise when a row cannot be resolved, instead of silently
                shrinking the dataset.
            require_mania_4k: reject charts whose ``Mode`` is not mania or whose
                ``CircleSize`` is not 4.
        """
        csv_file = Path(csv_path).resolve()
        if not csv_file.exists():
            raise FileNotFoundError(f"Benchmark CSV not found: {csv_file}")

        # Guard: reject binary spreadsheets masquerading as CSV (e.g. an .xlsx
        # saved under a .csv name). A plain-text CSV never starts with the ZIP
        # magic "PK" / OLE "D0 CF 11 E0" and never contains NUL bytes.
        with csv_file.open("rb") as fh:
            _head = fh.read(4096)
        if _head.startswith(b"PK") or _head.startswith(b"\xd0\xcf\x11\xe0") or b"\x00" in _head:
            raise ValueError(
                f"Benchmark file is not a plain-text CSV (binary/spreadsheet content detected): {csv_file}\n"
                "Hint: re-export it as a UTF-8 CSV file and retry."
            )

        if samples_root is None:
            samples_dir = csv_file.parent
        else:
            samples_dir = Path(samples_root).resolve()

        all_osu_files = sorted(samples_dir.glob("**/*.osu"))
        bid_map, meta_map = cls._index_files(all_osu_files)

        samples: List[BenchmarkSample] = []
        unresolved: List[Dict[str, Any]] = []
        seen_paths: Dict[Path, int] = {}

        # newline="" is required by the csv module; text=... would also be fine
        # but the explicit newline keeps embedded CR out of fields.
        with open(csv_file, "r", encoding="utf-8-sig", newline="") as fp:
            reader = csv.DictReader(fp)
            fieldnames = reader.fieldnames or []
            missing_cols = [c for c in (*_REQUIRED_COLUMNS, expected_column) if c not in fieldnames]
            if missing_cols:
                hint = ""
                if "expectedinsignicial" in fieldnames:
                    hint = (
                        "\nHint: this looks like the Signicial scale "
                        "(samples/4k/signicials_bench.csv); load it with "
                        "expected_column='expectedinsignicial'. Its values are NOT "
                        "comparable with the ND-scaled uuzs_bench.csv."
                    )
                raise ValueError(
                    f"{csv_file.name}: missing column(s) {missing_cols}; "
                    f"present columns: {fieldnames}{hint}"
                )

            for line_no, row in enumerate(reader, start=2):
                name = (row.get("name") or "").strip()
                pattern = (row.get("pattern") or "").strip()
                sub_pattern = (row.get("subPattern") or "").strip()
                expected_str = (row.get(expected_column) or "").strip()
                if not expected_str or not name:
                    unresolved.append(
                        {"line": line_no, "name": name, "reason": "empty name/expected"}
                    )
                    continue

                try:
                    expected = float(expected_str)
                except ValueError:
                    unresolved.append(
                        {"line": line_no, "name": name, "reason": f"non-numeric expected {expected_str!r}"}
                    )
                    continue
                if not math.isfinite(expected):
                    unresolved.append(
                        {"line": line_no, "name": name, "reason": f"non-finite expected {expected!r}"}
                    )
                    continue

                bid_val: Optional[int] = None
                raw_bid = (row.get("bid") or "").strip()
                if raw_bid:
                    try:
                        bid_val = int(float(raw_bid))
                    except ValueError:
                        pass

                matched_path = cls._resolve_path(
                    row=row,
                    name=name,
                    pattern=pattern,
                    bid_val=bid_val,
                    samples_dir=samples_dir,
                    all_osu_files=all_osu_files,
                    bid_map=bid_map,
                )

                if matched_path is None or not matched_path.is_file():
                    unresolved.append(
                        {
                            "line": line_no,
                            "name": name,
                            "pattern": pattern,
                            "reason": "no matching .osu file",
                        }
                    )
                    continue

                if require_mania_4k:
                    meta = meta_map.get(matched_path) or cls._scan_meta(matched_path)
                    cls._validate_chart_meta(matched_path, meta)

                resolved = matched_path.resolve()
                if resolved in seen_paths:
                    raise ValueError(
                        f"{csv_file.name}:{line_no} duplicates the chart from line "
                        f"{seen_paths[resolved]}: {resolved}"
                    )
                seen_paths[resolved] = line_no

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

        if strict and unresolved:
            detail = "\n".join(
                f"  line {u['line']}: {u.get('name', '')!r} -> {u['reason']}"
                for u in unresolved[:10]
            )
            raise ValueError(
                f"{csv_file.name}: {len(unresolved)} row(s) could not be resolved "
                f"(loaded {len(samples)}). Refusing to run on a silently smaller dataset.\n{detail}"
            )

        dataset = cls(samples)
        dataset.unresolved = unresolved
        dataset.source_csv = csv_file
        return dataset

    @staticmethod
    def _validate_chart_meta(path: Path, meta: Dict[str, Any]) -> None:
        mode = meta.get("mode")
        if mode is not None and str(mode).strip() != "3":
            raise ValueError(
                f"{path}: Mode is {mode!r}, expected 3 (osu!mania). "
                "This benchmark only scores 4K mania charts."
            )
        if mode is None:
            import warnings

            warnings.warn(
                f"{path}: no Mode field; assuming mania", RuntimeWarning, stacklevel=3
            )
        cs = meta.get("circle_size")
        if cs is not None and cs != 4:
            raise ValueError(
                f"{path}: CircleSize is {cs}, expected 4. "
                "Non-4K charts would be folded into 4 columns and produce fake jacks."
            )

    @classmethod
    def _resolve_path(
        cls,
        row: Dict[str, str],
        name: str,
        pattern: str,
        bid_val: Optional[int],
        samples_dir: Path,
        all_osu_files: Sequence[Path],
        bid_map: Dict[str, Path],
    ) -> Optional[Path]:
        """Resolve a CSV row to a file, preferring deterministic matches."""
        # 0. Explicit relative path column (preferred, deterministic).
        raw_path = (row.get("path") or "").strip()
        if raw_path:
            normalised = raw_path.replace("\\", "/")
            cand = (samples_dir / normalised).resolve()
            try:
                cand.relative_to(samples_dir.resolve())
            except ValueError:
                cand = None  # type: ignore[assignment]
            if cand is not None and cand.is_file():
                return cand

        # 1. Match by bid
        if bid_val is not None and str(bid_val) in bid_map:
            return bid_map[str(bid_val)]

        # 2. Exact / case-insensitive filename match inside the pattern folder.
        pattern_folder = samples_dir / pattern
        if not pattern_folder.exists():
            for d in samples_dir.iterdir():
                if d.is_dir() and d.name.lower() == pattern.lower():
                    pattern_folder = d
                    break

        candidates = [
            pattern_folder / f"{name}.osu",
            pattern_folder / name,
            pattern_folder / f"{name} .osu",
        ]
        for cand in candidates:
            if cand.is_file():
                return cand

        # 3. Constrained fuzzy match: the file stem must equal the row name
        #    (case-insensitive).  The old `startswith(name[:15])` rule could bind
        #    a row to a *different* chart such as "X [Hard]" vs "X [Easy]".
        if pattern_folder.exists():
            for pf in pattern_folder.glob("*.osu"):
                if pf.stem.lower() == name.lower():
                    return pf

        # 4. Global exact-stem match (single unambiguous hit only).
        hits = [pf for pf in all_osu_files if pf.stem.lower() == name.lower()]
        if len(hits) == 1:
            return hits[0]

        return None
