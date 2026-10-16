"""Anti-collision Gaussian timing jitter mutator for beatmaps.

Applies micro timing perturbations to normal notes (HitCircles) while preserving:
1. Column assignments (no cross-column movement).
2. Temporal order (t_1 < t_2 < ... < t_n).
3. Minimum playable gap (delta t_min = 10ms).
4. LN immutability (Hold notes are completely untouched).
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional
from cab.parser.osu_parser import Beatmap, HitObject


def apply_jitter(
    beatmap: Beatmap,
    sigma_ms: float = 8.0,
    max_ms: float = 15.0,
    min_gap_ms: int = 10,
    seed: Optional[int] = None,
) -> Beatmap:
    """Apply column-preserving collision-free Gaussian jitter to normal HitCircles.

    Args:
        beatmap: Original Beatmap instance.
        sigma_ms: Standard deviation of Gaussian perturbation (ms).
        max_ms: Truncation threshold for perturbation (|delta| <= max_ms).
        min_gap_ms: Minimum required time gap between adjacent notes in the same column.
        seed: Random seed for deterministic reproducibility.
    """
    rng = random.Random(seed)
    mutated = beatmap.clone()

    # Group objects by column
    col_objs: Dict[int, List[HitObject]] = {}
    for ho in mutated.hit_objects:
        col_objs.setdefault(ho.column, []).append(ho)

    for col, objs in col_objs.items():
        # Sort objects in column by start time
        objs.sort(key=lambda o: o.time)
        n = len(objs)
        if n == 0:
            continue

        prev_end = -float("inf")

        for i in range(n):
            curr = objs[i]
            if curr.is_ln:
                # Long notes are not perturbed; update prev_end to this LN's end_time
                prev_end = max(prev_end, curr.end_time)
                continue

            orig_t = curr.time
            # Generate truncated normal perturbation
            delta = rng.gauss(0.0, sigma_ms)
            delta = max(-max_ms, min(max_ms, delta))
            target_t = orig_t + delta

            # Lower bound: must be at least prev_end + min_gap_ms
            lower_bound = prev_end + min_gap_ms

            # Upper bound: must be at most next_note.time - min_gap_ms
            if i + 1 < n:
                next_t = objs[i + 1].time
                upper_bound = next_t - min_gap_ms
            else:
                upper_bound = float("inf")

            # Clamp if bounds allow
            if lower_bound <= upper_bound:
                new_t = max(lower_bound, min(upper_bound, target_t))
            else:
                # If original notes were already closer than 2*min_gap_ms, keep original or mid-point
                new_t = orig_t

            curr.time = int(round(new_t))
            curr.end_time = curr.time
            prev_end = curr.time

    # Sort all hit objects globally by time, then column
    mutated.hit_objects.sort(key=lambda o: (o.time, o.x))
    return mutated
