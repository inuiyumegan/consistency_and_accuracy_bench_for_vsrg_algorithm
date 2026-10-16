"""Short Long-Note (LN) injection mutator.

Transforms normal HitCircles into short Hold Notes (duration 20ms - 100ms)
when sufficient column spacing exists (> 120ms).
Evaluates whether a rating algorithm inflates difficulty for release-tap artifacts.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional
from cab.parser.osu_parser import Beatmap, HitObject, HIT_TYPE_HOLD, HIT_TYPE_CIRCLE


def inject_short_ln(
    beatmap: Beatmap,
    min_duration_ms: int = 20,
    max_duration_ms: int = 100,
    min_gap_ms: int = 120,
    seed: Optional[int] = None,
) -> Beatmap:
    """Inject short Long Notes (Hold Notes) into normal HitCircles.

    Args:
        beatmap: Original Beatmap instance.
        min_duration_ms: Minimum LN duration (default 20ms).
        max_duration_ms: Maximum LN duration (default 100ms).
        min_gap_ms: Minimum gap to the next note in the same column to qualify (default 120ms).
        seed: Random seed for deterministic reproducibility.
    """
    rng = random.Random(seed)
    mutated = beatmap.clone()

    col_objs: Dict[int, List[HitObject]] = {}
    for ho in mutated.hit_objects:
        col_objs.setdefault(ho.column, []).append(ho)

    for col, objs in col_objs.items():
        objs.sort(key=lambda o: o.time)
        n = len(objs)
        if n == 0:
            continue

        for i in range(n):
            curr = objs[i]
            # Only convert normal HitCircles, ignore existing LNs
            if not curr.is_circle or curr.is_ln:
                continue

            t_curr = curr.time
            if i + 1 < n:
                t_next = objs[i + 1].time
                gap = t_next - t_curr
            else:
                gap = 1000  # End of map has plenty of room

            if gap >= min_gap_ms:
                # Generate random duration
                dur = rng.randint(min_duration_ms, max_duration_ms)
                # Keep at least 20ms release buffer before next note
                end_t = min(t_curr + dur, t_curr + gap - 20)
                if end_t > t_curr:
                    curr.type = (curr.type & ~HIT_TYPE_CIRCLE) | HIT_TYPE_HOLD
                    curr.end_time = int(end_t)

    mutated.hit_objects.sort(key=lambda o: (o.time, o.x))
    return mutated
