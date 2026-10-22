"""Anti-collision Gaussian timing jitter and arrangement probe mutators.

Applies micro timing perturbations to normal notes (HitCircles) while preserving:
1. Column assignments (no cross-column movement).
2. Temporal order (t_1 < t_2 < ... < t_n).
3. Minimum playable gap (delta t_min = 10ms) with symmetric midpoint bounds.
4. LN immutability (Hold notes are completely untouched).
5. **Per-note offsets**: every tap note draws its own delta, so a vertical
   chord may end up split by a few milliseconds.  This mirrors a real player's
   timing error (per finger, not per chord).  A brief chord-synchronous
   experiment on 2026-10-21 isolated timing from pattern but lifted AJ across
   the board (MinaCalc-0.75: 52.90 -> 80.91) and was reverted the same day;
   see ``apply_jitter`` for the numbers.
6. OD-adaptive judgment window scaling: caps offset at 25% of the osu!mania
   300 hit window W_300(OD) = 64 - 3*OD (~10.8ms at OD7, 10.0ms at OD8,
   9.1ms at OD9.2), keeping perturbations strictly imperceptible to accuracy.
7. Non-negative timestamps (a 2016-era map can start within 10ms of zero).

Also provides ``apply_arrangement_probe``, a macro-density-preserving temporal
bunching probe that pairs consecutive tap notes inside windows and pulls them
into micro-bursts (outside the 300 hit window) without changing total note count
or 400ms window density, exposing algorithms that only count window density and
ignore intra-window note arrangement.
"""

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple
from cab.parser.osu_parser import Beatmap, HitObject


def get_od_window_300(beatmap: Beatmap) -> float:
    """Return osu!mania 2nd-tier (300) hit window half-width in ms: 64 - 3 * OD."""
    od_str = beatmap.difficulty.get("OverallDifficulty", "8.0")
    try:
        od = float(od_str)
    except ValueError:
        od = 8.0
    od = max(5.0, min(10.0, od))
    return 64.0 - 3.0 * od


def _validate_jitter_args(max_ms: Optional[float], sigma_ms: Optional[float], min_gap_ms: int) -> None:
    if max_ms is not None and float(max_ms) <= 0:
        raise ValueError(f"max_ms must be positive, got {max_ms}")
    if sigma_ms is not None and float(sigma_ms) < 0:
        raise ValueError(f"sigma_ms must be non-negative, got {sigma_ms}")
    if min_gap_ms < 0:
        raise ValueError(f"min_gap_ms must be non-negative, got {min_gap_ms}")


def apply_jitter(
    beatmap: Beatmap,
    sigma_ms: Optional[float] = None,
    max_ms: Optional[float] = None,
    min_gap_ms: int = 10,
    seed: Optional[int] = None,
    od_adaptive: bool = True,
) -> Beatmap:
    """Apply column-preserving, per-note independent Gaussian jitter to circles.

    Each tap note draws its own offset, so a vertical chord can end up split by
    a few milliseconds.  That is deliberate: a player's timing error is per
    finger, not per chord, so the probe perturbs the way an actual unstable
    player would.  The trade-off is that a 10-20 ms split turns a chord into a
    short roll, i.e. it changes the *pattern* as well as the timing, and a
    pattern-aware algorithm legitimately reacts to that.

    History: 2026-10-21 briefly made the draw chord-synchronous (one offset per
    timestamp) to isolate timing from pattern.  That raised AJ across the board
    (MinaCalc-0.75: 52.90 -> 80.91) almost entirely by lifting the direction
    gate from 0.50 to 0.92 -- half the charts used to read *easier* once their
    chords were broken.  Reverted the same day: the per-note version is the
    intended definition.

    Args:
        beatmap: Original Beatmap instance.
        sigma_ms: Standard deviation of Gaussian perturbation (ms). When None,
            derived from ``max_ms`` as ``0.55 * max_ms``.
        max_ms: Truncation threshold for perturbation (|delta| <= max_ms). When
            None and ``od_adaptive=True``, set to ``0.25 * (64 - 3 * OD)``
            (~10ms for community standard OD 7.0 - 9.2).
        min_gap_ms: Minimum required time gap between adjacent notes in the same column.
        seed: Random seed for deterministic reproducibility.
        od_adaptive: Whether to scale default jitter bounds with the beatmap's OD.
    """
    _validate_jitter_args(max_ms, sigma_ms, min_gap_ms)

    rng = random.Random(seed)
    mutated = beatmap.clone()

    if max_ms is None:
        if od_adaptive:
            w300 = get_od_window_300(mutated)
            eff_max_ms = 0.25 * w300
        else:
            eff_max_ms = 10.0
    else:
        eff_max_ms = float(max_ms)

    if sigma_ms is None:
        eff_sigma_ms = 0.55 * eff_max_ms
    else:
        eff_sigma_ms = float(sigma_ms)

    # Group objects by column: notes in different columns never constrain each
    # other, so this is the only axis on which collisions have to be avoided.
    col_objs: Dict[int, List[HitObject]] = {}
    for ho in mutated.hit_objects:
        col_objs.setdefault(ho.column, []).append(ho)

    for objs in col_objs.values():
        objs.sort(key=lambda o: o.time)
        n = len(objs)
        if n == 0:
            continue

        orig_times = [o.time for o in objs]
        orig_ends = [o.end_time if o.is_ln else o.time for o in objs]
        prev_end = -float("inf")

        for i in range(n):
            curr = objs[i]
            if curr.is_ln:
                # Long notes are not perturbed; they still constrain neighbours.
                prev_end = max(prev_end, float(curr.end_time))
                continue

            orig_t = orig_times[i]
            delta = rng.gauss(0.0, eff_sigma_ms)
            delta = max(-eff_max_ms, min(eff_max_ms, delta))
            target_t = orig_t + delta

            # Symmetric midpoint bounds keep note i from crowding note i+1.
            lower_bound = prev_end + min_gap_ms
            if i > 0 and not objs[i - 1].is_ln:
                mid_prev = 0.5 * (orig_ends[i - 1] + orig_t) + 0.5 * min_gap_ms
                lower_bound = max(lower_bound, mid_prev)
            # A 2016-era chart can start within 10 ms of zero; a negative
            # timestamp is not representable in the .osu format.
            lower_bound = max(lower_bound, 0.0)

            if i + 1 < n:
                if objs[i + 1].is_ln:
                    upper_bound = orig_times[i + 1] - min_gap_ms
                else:
                    upper_bound = 0.5 * (orig_t + orig_times[i + 1]) - 0.5 * min_gap_ms
            else:
                upper_bound = float("inf")

            if lower_bound <= upper_bound:
                new_t = max(lower_bound, min(upper_bound, target_t))
            else:
                new_t = orig_t

            curr.time = max(0, int(round(new_t)))
            curr.end_time = curr.time
            prev_end = curr.time

    # Deterministic serialisation order.
    mutated.hit_objects.sort(key=lambda o: (o.time, o.x))
    return mutated


def apply_arrangement_probe(beatmap: Beatmap) -> Beatmap:
    """Apply macro-density-preserving intra-window temporal bunching probe.

    Pairs consecutive normal tap notes in each column separated by 45..220ms
    and pulls the second note 58% closer to the first (e.g. 80ms, 80ms -> 34ms, 126ms).
    Total note count, column assignments, and 400ms macro window note counts remain
    invariant, while smooth intervals are warped into high-speed minijack bursts
    outside the 300 hit window. Algorithms that only count window density (ignoring
    note arrangement) show near-zero response (~1.5%), whereas pattern/speed-aware
    algorithms respond strongly (~12% - 23%).
    """
    mutated = beatmap.clone()
    col_objs: Dict[int, List[HitObject]] = {}
    for ho in mutated.hit_objects:
        col_objs.setdefault(ho.column, []).append(ho)

    for objs in col_objs.values():
        objs.sort(key=lambda o: o.time)
        n = len(objs)
        i = 0
        while i + 1 < n:
            a, b = objs[i], objs[i + 1]
            if a.is_ln or b.is_ln:
                i += 1
                continue
            gap = b.time - a.time
            if 45 <= gap <= 220:
                new_gap = max(24, int(round(gap * 0.42)))
                b.time = max(0, a.time + new_gap)
                b.end_time = b.time
                i += 2
            else:
                i += 1

    mutated.hit_objects.sort(key=lambda o: (o.time, o.x))
    return mutated
