"""Rate scaling mutator for beatmaps (osu! DoubleTime / Clock Rate model).

Applies DT rate factor r in (0.5, 3.0] where r > 1.0 speeds up the beatmap.
HitObject timestamps and TimingPoints are scaled proportionally by 1/r.
"""

from __future__ import annotations

import copy
import math
from typing import Sequence
from cab.parser.osu_parser import Beatmap, HitObject, TimingPoint


def scale_rate(beatmap: Beatmap, rate: float, rounding: str = "round") -> Beatmap:
    """Scale beatmap by clock rate factor r (e.g. 1.0 to 2.00).

    Strictly adheres to osu! DT Mod transformation:
    - HitObject.StartTime' = quantize(HitObject.StartTime / r)
    - HitObject.EndTime' = quantize(HitObject.EndTime / r)
    - TimingPoint.Time' = TimingPoint.Time / r
    - TimingPoint.BeatLength' = TimingPoint.BeatLength / r (for uninherited timing points)

    ``rounding`` selects the 1 ms quantizer: ``"round"`` (the osu!-faithful
    convention) or ``"floor"``.  The two differ by at most one millisecond per
    timestamp, which is enough to flip the sign of a sub-1% difficulty step --
    that spread is exactly what the anti-inversion audit uses to estimate the
    quantization noise floor of a given rate point.
    """
    if rate <= 0:
        raise ValueError(f"Rate scaling factor must be positive, got {rate}")
    if rounding not in ("round", "floor"):
        raise ValueError(f"rounding must be 'round' or 'floor', got {rounding!r}")

    if rounding == "floor":
        quantize = lambda value: int(math.floor(value))  # noqa: E731
    else:
        quantize = lambda value: int(round(value))  # noqa: E731

    # Shallow copy + rebuilt lists instead of Beatmap.clone() (a full
    # ``copy.deepcopy`` that costs ~18 ms on a 3.5k-object chart, i.e. 75% of
    # the entire per-evaluation Python overhead).  Only the fields this mutator
    # actually writes are copied; ``general`` / ``difficulty`` / ``events`` /
    # ``raw_sections`` stay shared because they are read-only downstream
    # (``serialize`` never writes to them).
    mutated = copy.copy(beatmap)
    mutated.metadata = dict(beatmap.metadata)
    inv_r = 1.0 / rate

    # Scale HitObjects
    scaled_objects = []
    for ho in beatmap.hit_objects:
        new_ho = copy.copy(ho)
        new_ho.time = quantize(ho.time * inv_r)
        if ho.is_ln:
            new_ho.end_time = quantize(ho.end_time * inv_r)
            # Ensure positive LN duration
            if new_ho.end_time <= new_ho.time:
                new_ho.end_time = new_ho.time + 1
        else:
            new_ho.end_time = new_ho.time
        scaled_objects.append(new_ho)
    mutated.hit_objects = scaled_objects

    # Scale TimingPoints
    scaled_points = []
    for tp in beatmap.timing_points:
        new_tp = copy.copy(tp)
        new_tp.time = tp.time * inv_r
        # uninherited == 1 is BPM timing point (beatLength is ms per beat)
        # uninherited == 0 is inherited SV point (beatLength is negative percentage, e.g. -100 for 1.0x SV)
        # Only scale beat length if it's an uninherited BPM point (positive)
        if tp.uninherited == 1 or tp.beat_length > 0:
            new_tp.beat_length = tp.beat_length * inv_r
        scaled_points.append(new_tp)
    mutated.timing_points = scaled_points

    # Update metadata to reflect rate
    orig_ver = beatmap.metadata.get("Version", "")
    mutated.metadata["Version"] = f"{orig_ver} ({rate:.3f}x)"

    return mutated


def generate_rate_series(
    beatmap: Beatmap,
    rates: Sequence[float],
) -> list[tuple[float, Beatmap]]:
    """Generate a series of rate-scaled beatmap variants."""
    return [(r, scale_rate(beatmap, r)) for r in rates]
