"""High-performance .osu format parser and AST representation for VSRG Mania beatmaps.

Supports in-memory manipulation, clone, mutation, and fast string serialization.

Parsing policy (see CODE_REVIEW_2026-10-21.md, P0-07 / P1-27..33 / P2-01..09):

* The 6th hit-object field is interpreted by **type bit**, never by segment
  count.  A circle's field is a verbatim hitSample (it may legitimately carry
  extra trailing segments); only a hold prepends ``endTime``.
* A *file* source that cannot be read, or a real beatmap with no hit objects,
  raises instead of silently yielding an empty map.  In-memory strings stay
  lenient so fixtures and mutated content can still be parsed.
* Sections that the writer does not know about (``[Colours]``, ``[Variables]``,
  ...) are preserved verbatim rather than dropped.
"""

from __future__ import annotations

import copy
import math
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Union


HIT_TYPE_CIRCLE = 1
HIT_TYPE_SLIDER = 2
HIT_TYPE_SPINNER = 8
HIT_TYPE_HOLD = 128

#: ``[Section]`` headers, tolerating a trailing ``// comment``.
_SECTION_RE = re.compile(r"^\[([^\]]+)\]\s*(?://.*)?$")

#: Sections that :meth:`Beatmap.serialize` knows how to write.
_KNOWN_SECTIONS = {
    "General",
    "Editor",
    "Metadata",
    "Difficulty",
    "Events",
    "TimingPoints",
    "HitObjects",
}


def _warn(message: str) -> None:
    """Emit a de-duplicated parser warning (same line => shown once)."""
    warnings.warn(message, RuntimeWarning, stacklevel=3)


def _as_int(value: str, default: int) -> int:
    """Tolerant int field parse: accepts ``4``, ``4.0`` and `` 4 ``."""
    text = value.strip()
    if not text:
        return default
    return int(float(text))


@dataclass(slots=True)
class TimingPoint:
    time: float
    beat_length: float
    meter: int = 4
    sample_set: int = 0
    sample_index: int = 0
    volume: int = 100
    uninherited: int = 1
    effects: int = 0

    @classmethod
    def from_line(cls, line: str) -> Optional[TimingPoint]:
        line = line.strip()
        if not line or line.startswith("//"):
            return None
        parts = line.split(",")
        if len(parts) < 2:
            return None
        try:
            t = float(parts[0])
            bl = float(parts[1])
            # Integer fields are parsed through float() for the same tolerance
            # the hit-object parser uses; a literal "4.0" must not drop the
            # whole timing point (it carries BPM/SV for the entire map).
            m = _as_int(parts[2], 4) if len(parts) > 2 else 4
            ss = _as_int(parts[3], 0) if len(parts) > 3 else 0
            si = _as_int(parts[4], 0) if len(parts) > 4 else 0
            vol = _as_int(parts[5], 100) if len(parts) > 5 else 100
            uninh = _as_int(parts[6], 1) if len(parts) > 6 else 1
            eff = _as_int(parts[7], 0) if len(parts) > 7 else 0
            return cls(t, bl, m, ss, si, vol, uninh, eff)
        except (ValueError, IndexError):
            _warn(f"osu_parser: dropped unparsable timing point {line!r}")
            return None

    def to_line(self) -> str:
        # Avoid unnecessary decimals if integer
        t_str = f"{self.time:.3f}".rstrip("0").rstrip(".") if self.time % 1 != 0 else str(int(self.time))
        bl_str = f"{self.beat_length:.6f}".rstrip("0").rstrip(".") if self.beat_length % 1 != 0 else str(int(self.beat_length))
        return f"{t_str},{bl_str},{self.meter},{self.sample_set},{self.sample_index},{self.volume},{self.uninherited},{self.effects}"


@dataclass(slots=True)
class HitObject:
    x: int
    y: int
    time: int
    type: int
    hit_sound: int
    end_time: int = 0
    sample_info: str = "0:0:0:0:"
    key_count: int = 4

    @property
    def column(self) -> int:
        col = int(math.floor(self.x * self.key_count / 512.0))
        return max(0, min(self.key_count - 1, col))

    @property
    def is_circle(self) -> bool:
        return bool(self.type & HIT_TYPE_CIRCLE)

    @property
    def is_ln(self) -> bool:
        return bool(self.type & HIT_TYPE_HOLD)

    @property
    def duration(self) -> int:
        if self.is_ln:
            return max(0, self.end_time - self.time)
        return 0

    @classmethod
    def from_line(cls, line: str, key_count: int = 4) -> Optional[HitObject]:
        line = line.strip()
        if not line or line.startswith("//"):
            return None
        parts = line.split(",")
        if len(parts) < 4:
            return None
        try:
            x = int(float(parts[0]))
            y = int(float(parts[1]))
            # osu! quantises to the millisecond with floor(x + 0.5); Python's
            # round() is banker's rounding and would nudge every .5 case down.
            t = int(math.floor(float(parts[2]) + 0.5))
            obj_type = _as_int(parts[3], HIT_TYPE_CIRCLE)
            hit_sound = _as_int(parts[4], 0) if len(parts) > 4 else 0

            end_time = t
            sample_info = "0:0:0:0:"
            if len(parts) > 5:
                extra = parts[5].strip()
                # The 6th field is ``objParams:hitSample``:
                #   circle -> hitSample only  (normalSet:additionSet:index:volume:filename)
                #   hold   -> endTime:hitSample
                # Deciding by segment count is WRONG: a hitSample may carry an
                # extra trailing segment (e.g. "0:0:0:0::"), and treating it as
                # a hold then strips the first segment, yielding volume="" which
                # strict parsers (rosu-pp) reject for the whole beatmap.
                if obj_type & HIT_TYPE_HOLD:
                    segs = extra.split(":")
                    # Canonical hold: endTime + 5-segment hitSample.
                    if len(segs) >= 6:
                        head, sample_info = segs[0], ":".join(segs[1:])
                        try:
                            end_time = int(math.floor(float(head) + 0.5))
                        except ValueError:
                            end_time = t
                            _warn(f"osu_parser: hold with non-numeric endTime {head!r}")
                    else:
                        # Malformed hold (no usable endTime): keep the field
                        # verbatim instead of corrupting the hitSample.
                        sample_info = extra
                        _warn(
                            "osu_parser: hold object without endTime segment "
                            f"({extra!r}); duration treated as 0"
                        )
                else:
                    # Circle: never interpret anything as an endTime.
                    sample_info = extra

                if obj_type & HIT_TYPE_HOLD and end_time < t:
                    _warn(
                        f"osu_parser: hold endTime {end_time} precedes start {t}; "
                        "duration will read as 0"
                    )

            return cls(
                x=x,
                y=y,
                time=t,
                type=obj_type,
                hit_sound=hit_sound,
                end_time=end_time,
                sample_info=sample_info,
                key_count=key_count,
            )
        except (ValueError, IndexError):
            _warn(f"osu_parser: dropped unparsable hit object {line!r}")
            return None

    def to_line(self) -> str:
        if self.is_ln:
            return f"{self.x},{self.y},{self.time},{self.type},{self.hit_sound},{self.end_time}:{self.sample_info}"
        else:
            return f"{self.x},{self.y},{self.time},{self.type},{self.hit_sound},{self.sample_info}"


@dataclass
class Beatmap:
    format_version: str = "osu file format v14"
    general: Dict[str, str] = field(default_factory=dict)
    editor: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, str] = field(default_factory=dict)
    difficulty: Dict[str, str] = field(default_factory=dict)
    events: List[str] = field(default_factory=list)
    timing_points: List[TimingPoint] = field(default_factory=list)
    hit_objects: List[HitObject] = field(default_factory=list)
    raw_sections: Dict[str, List[str]] = field(default_factory=dict)
    file_path: Optional[str] = None

    @property
    def key_count(self) -> int:
        cs = self.difficulty.get("CircleSize")
        if cs is not None:
            try:
                return int(float(cs))
            except ValueError:
                pass
        return 4

    @key_count.setter
    def key_count(self, value: int) -> None:
        self.difficulty["CircleSize"] = str(value)
        for obj in self.hit_objects:
            obj.key_count = value

    @property
    def duration_seconds(self) -> float:
        """Playable audio span of beatmap in seconds.

        An empty beatmap raises instead of returning a fake 1.0-second span:
        that fake denominator silently inflated every ``ms/s`` speed reading by
        several orders of magnitude (CODE_REVIEW P1-28).
        """
        if not self.hit_objects:
            raise ValueError(
                f"beatmap {self.file_path or '<memory>'} has no hit objects; "
                "duration is undefined"
            )
        t_min = min(obj.time for obj in self.hit_objects)
        t_max = max(max(obj.time, obj.end_time) for obj in self.hit_objects)
        return max(0.0, (t_max - t_min) / 1000.0)

    @classmethod
    def parse(cls, source: Union[str, Path], require_hit_objects: Optional[bool] = None) -> Beatmap:
        """Parse from a file path or from raw string content.

        ``require_hit_objects`` defaults to ``True`` when ``source`` is read
        from disk (a real chart must contain notes) and ``False`` for in-memory
        content, which is how fixtures and mutated buffers are parsed.
        """
        content: str
        file_path: Optional[str] = None

        is_path_obj = isinstance(source, Path)
        text = None if is_path_obj else str(source)
        looks_like_path = (
            is_path_obj
            or ("\n" not in text and text.lower().endswith(".osu"))
        )

        if looks_like_path:
            p = Path(source)
            if not p.is_file():
                raise FileNotFoundError(f"beatmap file not found: {p}")
            file_path = str(p.resolve())
            # utf-8-sig strips a leading BOM, which otherwise hid the
            # "osu file format vNNN" header and downgraded v128 maps to v14.
            with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
                content = f.read()
        elif is_path_obj:
            raise FileNotFoundError(f"beatmap file not found: {source}")
        else:
            content = text
            if "\n" not in content and not content.strip():
                raise ValueError("empty beatmap content")

        if require_hit_objects is None:
            require_hit_objects = file_path is not None

        beatmap = cls(file_path=file_path)
        lines = content.splitlines()
        current_section: Optional[str] = None
        section_lines: Dict[str, List[str]] = {}

        if lines:
            header = lines[0].lstrip("\ufeff").strip()
            if header.lower().startswith("osu file format"):
                beatmap.format_version = header

        # Pre-pass to find CircleSize so HitObjects have proper key_count
        detected_keys = 4
        for line in lines:
            line_s = line.strip()
            if line_s.startswith("CircleSize:"):
                try:
                    detected_keys = int(float(line_s.split(":", 1)[1].strip()))
                except ValueError:
                    pass
                break

        for line in lines:
            line_s = line.strip()
            if not line_s:
                continue
            header_match = _SECTION_RE.match(line_s)
            if header_match:
                current_section = header_match.group(1).strip()
                if current_section not in section_lines:
                    section_lines[current_section] = []
                continue

            if current_section is None:
                continue

            section_lines.setdefault(current_section, []).append(line)

        # Parse key-value sections
        for sec_name, target_dict in [
            ("General", beatmap.general),
            ("Editor", beatmap.editor),
            ("Metadata", beatmap.metadata),
            ("Difficulty", beatmap.difficulty),
        ]:
            if sec_name in section_lines:
                for line in section_lines[sec_name]:
                    line_s = line.strip()
                    if ":" in line_s and not line_s.startswith("//"):
                        k, v = line_s.split(":", 1)
                        target_dict[k.strip()] = v.strip()

        # Ensure key count is set
        if "CircleSize" not in beatmap.difficulty:
            beatmap.difficulty["CircleSize"] = str(detected_keys)
        actual_keys = beatmap.key_count

        # Parse TimingPoints
        if "TimingPoints" in section_lines:
            for line in section_lines["TimingPoints"]:
                tp = TimingPoint.from_line(line)
                if tp is not None:
                    beatmap.timing_points.append(tp)

        # Parse HitObjects
        if "HitObjects" in section_lines:
            for line in section_lines["HitObjects"]:
                ho = HitObject.from_line(line, key_count=actual_keys)
                if ho is not None:
                    beatmap.hit_objects.append(ho)

        if "Events" in section_lines:
            beatmap.events = section_lines["Events"]

        beatmap.raw_sections = section_lines

        if require_hit_objects and not beatmap.hit_objects:
            raise ValueError(
                f"{file_path or '<memory>'}: no [HitObjects] entries "
                "(truncated file, error page, or unsupported mode?)"
            )

        if "HitObjects" not in section_lines:
            _warn(f"osu_parser: {file_path or '<memory>'} has no [HitObjects] section")

        return beatmap

    def clone(self) -> Beatmap:
        """Deep copy for mutation without affecting the original."""
        return copy.deepcopy(self)

    def serialize(self) -> str:
        """Serialize beatmap AST back to valid .osu format string."""
        lines: List[str] = [self.format_version, ""]

        def write_kv_section(name: str, d: Dict[str, str]):
            lines.append(f"[{name}]")
            for k, v in d.items():
                lines.append(f"{k}:{v}")
            lines.append("")

        write_kv_section("General", self.general)
        if self.editor:
            write_kv_section("Editor", self.editor)
        write_kv_section("Metadata", self.metadata)
        write_kv_section("Difficulty", self.difficulty)

        lines.append("[Events]")
        for ev in self.events:
            lines.append(ev)
        lines.append("")

        lines.append("[TimingPoints]")
        for tp in self.timing_points:
            lines.append(tp.to_line())
        lines.append("")

        # Sections we do not model ([Colours], [Variables], ...) are preserved
        # verbatim, but they must keep their canonical position *before*
        # [HitObjects]: emitting them last produced a file whose object block
        # ended in a section header, and a parser that reads until the next
        # header (Dan-Overlay's .osu reader) then died on
        # "could not convert string to float: '[Colours]'".  That silently
        # dropped 7 of its 300 probe charts on 2026-10-21.  59 of the 856
        # charts carry [Colours]; for every parser that keys off section
        # headers this reordering is a no-op, for the strict ones it is the
        # difference between a reading and None.
        for sec_name, sec_lines in self.raw_sections.items():
            if sec_name in _KNOWN_SECTIONS:
                continue
            lines.append(f"[{sec_name}]")
            lines.extend(sec_lines)
            lines.append("")

        # Sort a copy: mutating self.hit_objects here used to reorder a cached
        # Beatmap shared with BenchmarkSample (CODE_REVIEW P3).
        ordered = sorted(self.hit_objects, key=lambda ho: (ho.time, ho.x))

        lines.append("[HitObjects]")
        for ho in ordered:
            lines.append(ho.to_line())
        lines.append("")

        return "\n".join(lines)
