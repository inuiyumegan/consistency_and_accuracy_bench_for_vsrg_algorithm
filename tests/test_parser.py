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


    def test_unknown_section_stays_before_hit_objects(self):
        """[Colours] must not be re-emitted after [HitObjects].

        Emitting preserved-but-unmodelled sections last produced a file whose
        object block ended in a header.  Tolerant parsers do not care, but a
        parser that reads objects until the next header (Dan-Overlay) then dies
        with "could not convert string to float: '[Colours]'" -- which silently
        cost 7 of its 300 jitter probe charts on 2026-10-21.
        """
        text = "\n".join([
            "osu file format v14",
            "",
            "[General]",
            "Mode:3",
            "",
            "[Metadata]",
            "Title:x",
            "",
            "[Difficulty]",
            "CircleSize:4",
            "",
            "[TimingPoints]",
            "0,500,4,2,0,100,1,0",
            "",
            "[Colours]",
            "Combo1 : 255,0,0",
            "",
            "[HitObjects]",
            "64,192,1000,1,0,0:0:0:0:",
            "192,192,1500,1,0,0:0:0:0:",
            "",
        ])
        out = Beatmap.parse(text).serialize()
        self.assertLess(out.index("[Colours]"), out.index("[HitObjects]"))
        # The section itself is still preserved verbatim.
        self.assertIn("Combo1 : 255,0,0", out)


if __name__ == "__main__":
    unittest.main()
