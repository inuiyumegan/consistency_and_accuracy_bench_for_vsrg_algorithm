import unittest
from pathlib import Path
from cab.parser.osu_parser import Beatmap
from cab.mutator.rate import scale_rate
from cab.mutator.jitter import apply_jitter
from cab.mutator.short_ln import inject_short_ln


class TestMutator(unittest.TestCase):
    def setUp(self):
        self.sample = Path("samples/4k/speed/498 Tokio(signicial dan).osu")
        self.bm = Beatmap.parse(self.sample)

    def test_rate_scaling(self):
        scaled = scale_rate(self.bm, 1.5)
        self.assertEqual(len(scaled.hit_objects), len(self.bm.hit_objects))
        for orig, mut in zip(self.bm.hit_objects, scaled.hit_objects):
            self.assertEqual(mut.time, int(round(orig.time / 1.5)))

    def test_jitter_collision_free(self):
        jittered = apply_jitter(self.bm, sigma_ms=8.0, max_ms=15.0, min_gap_ms=10, seed=123)
        self.assertEqual(len(jittered.hit_objects), len(self.bm.hit_objects))
        for col in range(4):
            col_objs = [o for o in jittered.hit_objects if o.column == col]
            for i in range(len(col_objs) - 1):
                self.assertLessEqual(col_objs[i].time, col_objs[i + 1].time)

    def test_short_ln_injection(self):
        orig_ln_count = sum(1 for o in self.bm.hit_objects if o.is_ln)
        injected = inject_short_ln(self.bm, min_duration_ms=20, max_duration_ms=100, seed=123)
        new_ln_count = sum(1 for o in injected.hit_objects if o.is_ln)
        self.assertGreater(new_ln_count, orig_ln_count)


if __name__ == "__main__":
    unittest.main()
