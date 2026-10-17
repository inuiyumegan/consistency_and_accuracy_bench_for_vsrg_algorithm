"""Unit tests for the adaptive two-phase rate sweep (suspicious interval detection)."""

import unittest

from cab.runner.suite import _find_suspicious_intervals


class TestSuspiciousIntervals(unittest.TestCase):
    def test_monotonic_curve_clean(self):
        curve = [(1.0 + 0.1 * i, 10.0 + i) for i in range(10)]
        self.assertEqual(_find_suspicious_intervals(curve), [])

    def test_nearly_linear_curve_clean(self):
        # Median-gradient outlier check must not fire on a smooth linear ramp
        curve = [(1.0 + 0.05 * i, 8.0 + 0.4 * i) for i in range(20)]
        self.assertEqual(_find_suspicious_intervals(curve), [])

    def test_non_monotonic_pair_detected_with_neighbors(self):
        curve = [(1.1, 10.0), (1.2, 10.5), (1.3, 10.4), (1.4, 11.0)]
        intervals = _find_suspicious_intervals(curve)
        self.assertIn((1.2, 1.3), intervals)
        self.assertIn((1.1, 1.2), intervals)
        self.assertIn((1.3, 1.4), intervals)

    def test_gradient_stagnation_detected(self):
        # Normal slope is +10.0 per rate unit (+1.0 per 0.1 step), but [1.2, 1.3] stalls to +0.05
        curve = [(1.0, 10.0), (1.1, 11.0), (1.2, 12.0), (1.3, 12.05), (1.4, 13.05), (1.5, 14.05)]
        intervals = _find_suspicious_intervals(curve)
        self.assertIn((1.2, 1.3), intervals)

    def test_gradient_spike_detected(self):
        ds = [10.0, 10.01, 12.0, 12.01, 12.02]
        curve = [(1.0 + 0.1 * i, ds[i]) for i in range(5)]
        intervals = _find_suspicious_intervals(curve)
        self.assertIn((1.1, 1.2), intervals)
        # Neighborhood intervals should also be flagged
        self.assertIn((1.0, 1.1), intervals)
        self.assertIn((1.2, 1.3), intervals)

    def test_short_curve_ignored(self):
        self.assertEqual(_find_suspicious_intervals([]), [])
        self.assertEqual(_find_suspicious_intervals(None), [])
        self.assertEqual(_find_suspicious_intervals([(1.0, 5.0), (1.1, 6.0)]), [])

    def test_flat_curve_no_false_positive(self):
        curve = [(1.0 + 0.1 * i, 5.0) for i in range(8)]
        self.assertEqual(_find_suspicious_intervals(curve), [])


if __name__ == "__main__":
    unittest.main()
