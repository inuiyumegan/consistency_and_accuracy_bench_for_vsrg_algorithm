"""High-performance .osu format parser and AST representation for VSRG Mania beatmaps.

Supports in-memory manipulation, clone, mutation, and fast string serialization.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Dict, Optional, Union


HIT_TYPE_CIRCLE = 1
HIT_TYPE_SLIDER = 2
HIT_TYPE_SPINNER = 8
HIT_TYPE_HOLD = 128


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
            m = int(parts[2]) if len(parts) > 2 else 4
            ss = int(parts[3]) if len(parts) > 3 else 0
            si = int(parts[4]) if len(parts) > 4 else 0
            vol = int(parts[5]) if len(parts) > 5 else 100
            uninh = int(parts[6]) if len(parts) > 6 else 1
            eff = int(parts[7]) if len(parts) > 7 else 0
            return cls(t, bl, m, ss, si, vol, uninh, eff)
        except (ValueError, IndexError):
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
            t = int(round(float(parts[2])))
            obj_type = int(parts[3])
            hit_sound = int(parts[4]) if len(parts) > 4 else 0

            end_time = t
            sample_info = "0:0:0:0:"
            if len(parts) > 5:
                extra = parts[5].strip()
                # osu! hit-object spec for the 6th field is
                #   objParams:hitSample
                # where hitSample is ALWAYS 5 segments:
                #   normalSet:additionSet:index:volume:filename
                # (the trailing filename is often empty, hence the trailing ":").
                #
                # A hold (LN) prepends objParams = endTime, so the field looks
                # like "endTime:normalSet:additionSet:index:volume:filename"
                # (6 segments). A circle has no endTime, so the field is just
                # the 5-segment hitSample.
                #
                # Splitting must therefore keep the trailing 5 segments intact;
                # blindly stripping the first segment corrupts circles
                # ("0:0:0:0:" -> "0:0:0:"), which strict parsers (e.g. rosu-pp)
                # reject outright.
                if ":" in extra:
                    segs = extra.split(":")
                    if len(segs) > 5:
                        # endTime + hitSample(5); anything extra is folded back
                        # to preserve the original tail verbatim.
                        head, sample_info = segs[0], ":".join(segs[1:])
                        if head:
                            try:
                                end_time = int(round(float(head)))
                            except ValueError:
                                end_time = t
                    else:
                        # No endTime: the whole field is the hitSample.
                        sample_info = extra
                else:
                    # No ":" at all: treat the field as an endTime (defensive).
                    sample_info = "0:0:0:0:"
                    try:
                        end_time = int(round(float(extra)))
                    except ValueError:
                        end_time = t

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
        """Playable audio span of beatmap in seconds."""
        if not self.hit_objects:
            return 1.0
        t_min = min(obj.time for obj in self.hit_objects)
        t_max = max(max(obj.time, obj.end_time) for obj in self.hit_objects)
        return max(1.0, (t_max - t_min) / 1000.0)

    @classmethod
    def parse(cls, source: Union[str, Path]) -> Beatmap:
        """Parse from file path or raw string content."""
        content: str
        file_path: Optional[str] = None
        if isinstance(source, Path) or (isinstance(source, str) and "\n" not in source and Path(source).exists()):
            p = Path(source)
            file_path = str(p.resolve())
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            content = str(source)

        beatmap = cls(file_path=file_path)
        lines = content.splitlines()
        current_section: Optional[str] = None
        section_lines: Dict[str, List[str]] = {}

        if lines and lines[0].startswith("osu file format"):
            beatmap.format_version = lines[0].strip()

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
            if line_s.startswith("[") and line_s.endswith("]"):
                current_section = line_s[1:-1].strip()
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

        # Ensure hit objects are sorted by time, then x
        self.hit_objects.sort(key=lambda ho: (ho.time, ho.x))

        lines.append("[HitObjects]")
        for ho in self.hit_objects:
            lines.append(ho.to_line())
        lines.append("")

        return "\n".join(lines)
