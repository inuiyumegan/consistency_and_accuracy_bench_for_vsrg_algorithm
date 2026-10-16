import unittest
from pathlib import Path
from cab.parser.osu_parser import Beatmap, HitObject, TimingPoint, HIT_TYPE_HOLD, HIT_TYPE_CIRCLE


class TestOsuParser(unittest.TestCase):
    def test_timing_point_line(self):
        tp = TimingPoint(time=1000.0, beat_length=500.0, meter=4, volume=80)
        line = tp.to_line()
        tp2 = TimingPoint.from_line(line)
        self.assertIsNotNone(tp2)
        self.assertEqual(tp2.time, 1000.0)
        self.assertEqual(tp2.beat_length, 500.0)
        self.assertEqual(tp2.volume, 80)

    def test_hit_object_parsing(self):
        # 4K HitCircle in column 0 (x=64)
        c_line = "64,192,2000,1,0,0:0:0:0:"
        ho_c = HitObject.from_line(c_line, key_count=4)
        self.assertIsNotNone(ho_c)
        self.assertEqual(ho_c.column, 0)
        self.assertTrue(ho_c.is_circle)
        self.assertFalse(ho_c.is_ln)
        self.assertEqual(ho_c.time, 2000)

        # 4K Hold Note in column 3 (x=448)
        ln_line = "448,192,3000,128,0,3500:0:0:0:0:"
        ho_ln = HitObject.from_line(ln_line, key_count=4)
        self.assertIsNotNone(ho_ln)
        self.assertEqual(ho_ln.column, 3)
        self.assertFalse(ho_ln.is_circle)
        self.assertTrue(ho_ln.is_ln)
        self.assertEqual(ho_ln.duration, 500)

    def test_beatmap_parse_and_serialize(self):
        sample = Path("samples/4k/speed/498 Tokio(signicial dan).osu")
        if sample.exists():
            bm = Beatmap.parse(sample)
            self.assertEqual(bm.key_count, 4)
            self.assertGreater(len(bm.hit_objects), 100)
            text = bm.serialize()
            bm2 = Beatmap.parse(text)
            self.assertEqual(len(bm.hit_objects), len(bm2.hit_objects))
            self.assertEqual(len(bm.timing_points), len(bm2.timing_points))


if __name__ == "__main__":
    unittest.main()
