"""Tests for speed distribution metrics (p95/max) and environment disclosure."""

import unittest

from cab.metrics.speed_metric import evaluate_speed
from cab.report.environment import collect_environment_info


class TestSpeedDistribution(unittest.TestCase):
    def test_p95_and_max(self):
        # 100 samples on a 100s map -> speeds 0.01..1.00 ms/s
        timings = [(float(i + 1), 100.0) for i in range(100)]
        res = evaluate_speed(timings)
        self.assertEqual(res.evaluated_count, 100)
        # median of 0.01..1.00 = (0.50 + 0.51) / 2
        self.assertAlmostEqual(res.median_speed_ms_per_s, 0.505, places=4)
        # linear-interpolated p95: pos = 99 * 0.95 = 94.05 -> 0.9505
        self.assertAlmostEqual(res.p95_speed_ms_per_s, 0.9505, places=4)
        self.assertAlmostEqual(res.max_speed_ms_per_s, 1.0, places=4)

    def test_empty_timings_defaults(self):
        res = evaluate_speed([])
        self.assertEqual(res.p95_speed_ms_per_s, 0.0)
        self.assertEqual(res.max_speed_ms_per_s, 0.0)

    def test_single_sample(self):
        res = evaluate_speed([(5.0, 10.0)])
        self.assertAlmostEqual(res.p95_speed_ms_per_s, 0.5, places=4)
        self.assertAlmostEqual(res.max_speed_ms_per_s, 0.5, places=4)

    def test_existing_fields_unchanged(self):
        timings = [(float(i + 1), 100.0) for i in range(100)]
        res = evaluate_speed(timings)
        self.assertAlmostEqual(res.average_speed_ms_per_s, 0.505, places=4)
        self.assertAlmostEqual(res.realtime_factor, 1000.0 / 0.505, places=1)


class TestEnvironmentInfo(unittest.TestCase):
    def test_collect_environment_info(self):
        info = collect_environment_info()
        self.assertIn("cpu_model", info)
        self.assertIsInstance(info["cpu_model"], str)
        self.assertIsInstance(info["cpu_cores"], int)
        self.assertGreater(info["cpu_cores"], 0)
        self.assertIsInstance(info["memory_gb"], float)
        self.assertIn("os", info)
        self.assertIn("python_version", info)
        self.assertIn("note", info)


if __name__ == "__main__":
    unittest.main()
