"""Short Long-Note (LN) injection mutator.

Transforms normal HitCircles into short Hold Notes when sufficient column
spacing exists (> 120ms), then evaluates whether a rating algorithm inflates
difficulty for release-tap artifacts.

Injected duration: **49.25 - 58.75 ms** (2026-10-19).  The interval is centred
on 54 ms, which sits on the measured peak of players' natural hold time:
across 212,941 taps from 73 replays the per-map peak averaged 58.3 ms (4K),
52.5 ms (7K) and 61.2 ms (8K).  So every injected LN is something a player can
*actually* hold, while almost no mapper writes an LN this short (real LN p25 is
72 ms, median 110 ms) -- exactly the blind spot the dimension is meant to probe.

The old 20-100 ms range included 20-40 ms, a band players physically cannot
perform: only 2.3% of real taps are that short, and 0.1% are under 20 ms.
"""

from __future__ import annotations

import random
from typing import Dict, List, Optional
from cab.parser.osu_parser import Beatmap, HitObject, HIT_TYPE_HOLD, HIT_TYPE_CIRCLE


def inject_short_ln(
    beatmap: Beatmap,
    min_duration_ms: float = 49.25,
    max_duration_ms: float = 58.75,
    min_gap_ms: int = 120,
    seed: Optional[int] = None,
) -> Beatmap:
    """Inject short Long Notes (Hold Notes) into normal HitCircles.

    Args:
        beatmap: Original Beatmap instance.
        min_duration_ms: Minimum LN duration (default 49.25ms).
        max_duration_ms: Maximum LN duration (default 58.75ms).  Both are floats
            so the interval can be centred exactly on 54 ms; timestamps are
            rounded to whole milliseconds on write, giving 49-59 ms in practice.
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
                # Generate random duration (float interval, rounded on write)
                dur = rng.uniform(min_duration_ms, max_duration_ms)
                # Keep at least 20ms release buffer before next note
                end_t = min(t_curr + dur, t_curr + gap - 20)
                if end_t > t_curr:
                    curr.type = (curr.type & ~HIT_TYPE_CIRCLE) | HIT_TYPE_HOLD
                    curr.end_time = int(round(end_t))

    mutated.hit_objects.sort(key=lambda o: (o.time, o.x))
    return mutated
