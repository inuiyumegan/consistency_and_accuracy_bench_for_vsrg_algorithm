"""Rate scaling mutator for beatmaps (osu! DoubleTime / Clock Rate model).

Applies DT rate factor r in (0.5, 3.0] where r > 1.0 speeds up the beatmap.
HitObject timestamps and TimingPoints are scaled proportionally by 1/r.
"""

from __future__ import annotations

import math
from typing import Sequence
from cab.parser.osu_parser import Beatmap, HitObject, TimingPoint


def scale_rate(beatmap: Beatmap, rate: float) -> Beatmap:
    """Scale beatmap by clock rate factor r (e.g. 1.01 to 2.00).

    Strictly adheres to osu! DT Mod transformation:
    - HitObject.StartTime' = round(HitObject.StartTime / r)
    - HitObject.EndTime' = round(HitObject.EndTime / r)
    - TimingPoint.Time' = TimingPoint.Time / r
    - TimingPoint.BeatLength' = TimingPoint.BeatLength / r (for uninherited timing points)
    """
    if rate <= 0:
        raise ValueError(f"Rate scaling factor must be positive, got {rate}")

    mutated = beatmap.clone()
    inv_r = 1.0 / rate

    # Scale HitObjects
    for ho in mutated.hit_objects:
        ho.time = int(round(ho.time * inv_r))
        if ho.is_ln:
            ho.end_time = int(round(ho.end_time * inv_r))
            # Ensure positive LN duration
            if ho.end_time <= ho.time:
                ho.end_time = ho.time + 1
        else:
            ho.end_time = ho.time

    # Scale TimingPoints
    for tp in mutated.timing_points:
        tp.time = tp.time * inv_r
        # uninherited == 1 is BPM timing point (beatLength is ms per beat)
        # uninherited == 0 is inherited SV point (beatLength is negative percentage, e.g. -100 for 1.0x SV)
        # Only scale beat length if it's an uninherited BPM point (positive)
        if tp.uninherited == 1 or tp.beat_length > 0:
            tp.beat_length = tp.beat_length * inv_r

    # Update metadata to reflect rate
    orig_ver = mutated.metadata.get("Version", "")
    mutated.metadata["Version"] = f"{orig_ver} ({rate:.3f}x)"

    return mutated


def generate_rate_series(
    beatmap: Beatmap,
    rates: Sequence[float],
) -> list[tuple[float, Beatmap]]:
    """Generate a series of rate-scaled beatmap variants."""
    return [(r, scale_rate(beatmap, r)) for r in rates]
