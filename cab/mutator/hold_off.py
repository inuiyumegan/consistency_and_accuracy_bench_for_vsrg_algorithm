"""Hold Off (HO) Mutator for VSRG Mania Beatmaps.

Replicates the standard osu!mania Hold Off (HO) mod by converting all
Long Note (Hold) hit objects into normal Circle (Tap / Rice) hit objects.
Used by CAB-VA to probe whether an algorithm genuinely models LN semantics
or simply strips all LNs to tap notes.
"""

from __future__ import annotations

import copy
from cab.parser.osu_parser import Beatmap, HitObject, HIT_TYPE_CIRCLE


def apply_hold_off(beatmap: Beatmap) -> Beatmap:
    """Return a clone of the beatmap with all Long Notes converted to standard Rice Tap notes."""
    clone = copy.deepcopy(beatmap)
    new_objs = []
    for obj in clone.hit_objects:
        if obj.is_ln:
            new_objs.append(
                HitObject(
                    x=obj.x,
                    y=obj.y,
                    time=obj.time,
                    type=HIT_TYPE_CIRCLE,
                    hit_sound=obj.hit_sound,
                    end_time=0,
                    sample_info=obj.sample_info,
                    key_count=obj.key_count,
                )
            )
        else:
            new_objs.append(copy.copy(obj))

    clone.hit_objects = new_objs
    return clone
