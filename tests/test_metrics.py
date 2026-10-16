import unittest
import math
import numpy as np
from cab.metrics.calibration import weighted_pava, ScaleCalibrator, CalibrationResult
from cab.metrics.inversion import evaluate_rate_inversion, InversionResult
from cab.metrics.continuity import evaluate_continuity, ContinuityResult
from cab.metrics.jitter_metric import evaluate_jitter, JitterResult
from cab.metrics.short_ln_metric import evaluate_short_ln, ShortLNResult
from cab.metrics.speed_metric import evaluate_speed, calculate_speed_score, SpeedResult
from cab.metrics.scoring import calculate_cab_score, WEIGHT_ACCURACY, WEIGHT_INVERSION, WEIGHT_SHORT_LN, WEIGHT_CONTINUITY, WEIGHT_SPEED, WEIGHT_JITTER
from cab.metrics.accuracy import AccuracyResult


class TestMetrics(unittest.TestCase):
    def test_weighted_pava(self):
        y = np.array([1.0, 3.0, 2.0, 4.0])
        w = np.array([1.0, 1.0, 1.0, 1.0])
        res = weighted_pava(y, w)
        np.testing.assert_allclose(res, [1.0, 2.5, 2.5, 4.0])
        self.assertTrue(np.all(np.diff(res) >= 0))

    def test_scale_calibrator_with_macro_inversion(self):
        expected = np.array([1.0, 1.0, 2.0, 2.0, 3.0, 3.0])
        predicted = np.array([2.5, 2.5, 2.0, 2.0, 4.0, 4.0])

        calibrator = ScaleCalibrator()
        calib_res = calibrator.calibrate(expected, predicted, is_ng_native=False)
        self.assertEqual(len(calib_res.macro_alerts), 1)
        self.assertEqual(calib_res.macro_alerts[0].lower_tier, 1.0)
        self.assertEqual(calib_res.macro_alerts[0].higher_tier, 2.0)

        calib_preds = calib_res.predict(np.array([1.5, 2.0, 2.5, 3.0, 4.0]))
        self.assertTrue(np.all(np.diff(calib_preds) >= 0))

    def test_daniel_conversion_standard(self):
        calibrator = ScaleCalibrator()
        expected = np.array([11.0, 15.0, 18.0])
        predicted = np.array([11.5, 15.5, 18.5])
        calib_res = calibrator.calibrate(expected, predicted, scale_type="daniel")
        self.assertEqual(calib_res.conversion_formula, "daniel")
        converted = calib_res.predict(np.array([15.5, 18.5]))
        np.testing.assert_allclose(converted, [15.0, 18.0])

    def test_rate_inversion_evaluation(self):
        curves = {
            "map1": [(1.0, 3.0), (1.1, 3.3), (1.2, 3.6)],
            "map2": [(1.0, 4.0), (1.1, 5.0), (1.2, 4.5)],
        }
        res = evaluate_rate_inversion(curves)
        self.assertEqual(res.inverted_maps_count, 1)
        self.assertAlmostEqual(res.max_relative_drop, 0.1, places=2)

    def test_short_ln_native_immunity(self):
        orig = [3.0, 4.0, 5.0]
        sln = [3.0, 4.0, 5.0]
        res = evaluate_short_ln(orig, sln)
        self.assertTrue(res.has_native_immunity)
        self.assertIn("S+", res.immunity_grade)

    def test_short_ln_anti_stripping_penalty(self):
        orig = [3.0, 4.0, 5.0]
        sln = [3.0, 4.0, 5.0]  # numeric delta is 0
        # If is_brute_force_tap is True, immunity must be revoked and grade marked F
        res_brute = evaluate_short_ln(orig, sln, is_brute_force_tap=True)
        self.assertFalse(res_brute.has_native_immunity)
        self.assertIn("Brute-Force LN-to-Tap Stripping", res_brute.immunity_grade)

        # If ln_excluded is True, immunity must be revoked and grade marked F
        res_ex = evaluate_short_ln(orig, sln, ln_excluded=True)
        self.assertFalse(res_ex.has_native_immunity)
        self.assertIn("LN Excluded", res_ex.immunity_grade)

    def test_speed_metric_scoring(self):
        # <= 0.10 ms/s should be 100 pts
        self.assertEqual(calculate_speed_score(0.05), 100.0)
        self.assertEqual(calculate_speed_score(0.10), 100.0)
        # 1.00 ms/s should be 75.0 pts
        self.assertEqual(calculate_speed_score(1.00), 75.0)
        # 10.00 ms/s should be 50.0 pts
        self.assertEqual(calculate_speed_score(10.00), 50.0)
        # 100.00 ms/s should be 25.0 pts
        self.assertEqual(calculate_speed_score(100.00), 25.0)
        # >= 1000.00 ms/s should be 0.0 pts
        self.assertEqual(calculate_speed_score(1000.00), 0.0)
        self.assertEqual(calculate_speed_score(2500.00), 0.0)

        # evaluate_speed across samples
        timings = [
            (100.0, 100.0),  # 1.0 ms/s
            (50.0, 100.0),   # 0.5 ms/s
        ]
        s_res = evaluate_speed(timings)
        self.assertAlmostEqual(s_res.average_speed_ms_per_s, 0.75, places=2)
        self.assertTrue(s_res.speed_score > 75.0)

    def test_weights_and_100_point_composite_score(self):
        # Verify weight ordering: ACC > AIN > ASLN > CT = AJ > Speed
        self.assertGreater(WEIGHT_ACCURACY, WEIGHT_INVERSION)
        self.assertGreater(WEIGHT_INVERSION, WEIGHT_SHORT_LN)
        self.assertGreater(WEIGHT_SHORT_LN, WEIGHT_CONTINUITY)
        self.assertGreater(WEIGHT_CONTINUITY, WEIGHT_SPEED)
        self.assertGreater(WEIGHT_JITTER, WEIGHT_SPEED)
        self.assertEqual(WEIGHT_CONTINUITY, WEIGHT_JITTER)

        # Verify weights sum to 1.0
        total_weight = (
            WEIGHT_ACCURACY
            + WEIGHT_INVERSION
            + WEIGHT_SHORT_LN
            + WEIGHT_CONTINUITY
            + WEIGHT_SPEED
            + WEIGHT_JITTER
        )
        self.assertAlmostEqual(total_weight, 1.0, places=5)

    def test_anti_jitter_and_short_ln_rational_curves(self):
        from cab.metrics.calibration import CalibrationResult
        dummy_calib = CalibrationResult(is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.5, global_mae=0.4, spearman_rho=0.95, spearman_p=1e-5,
            kendall_tau=0.9, stratified_rmse={}, weighted_stratified_rmse=0.5,
            mean_wtd=0.5, wtd_by_tier={}, calibration=dummy_calib
        )
        inv = InversionResult(
            inversion_frequency=0.0, auc_deficit=0.0, max_relative_drop=0.0,
            max_absolute_drop=0.0, inverted_maps_count=0, total_maps_evaluated=10,
            composite_inversion_index=0.0
        )
        cont = ContinuityResult(
            cliff_events_count=0, max_lipschitz=5.0, mean_lipschitz=2.0, smoothness_score=100.0
        )

        # 1. Test AJ Rational Curve: S_jit = 100 / (1 + (mape/4.0)^1.6)
        # MAPE = 0.0% -> 100 pts
        jit_0 = JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 10)
        sln_base = ShortLNResult(0.0, 0.0, 0.0, True, "S+", 10)
        score_0 = calculate_cab_score(acc, inv, cont, jit_0, sln_base)
        self.assertEqual(score_0.s_jitter, 100.0)

        # MAPE = 4.0% -> exactly 50 pts
        jit_4 = JitterResult(0.3, 4.0, 0.5, 0.1, 0.01, 10)
        score_4 = calculate_cab_score(acc, inv, cont, jit_4, sln_base)
        self.assertAlmostEqual(score_4.s_jitter, 50.0, places=1)

        # MAPE = 1.06% (Interlude) -> ~89.3 pts (resilient)
        jit_inter = JitterResult(0.1, 1.06, 0.2, 0.05, 0.005, 10)
        score_inter = calculate_cab_score(acc, inv, cont, jit_inter, sln_base)
        self.assertTrue(88.0 < score_inter.s_jitter < 91.0)

        # MAPE = 3.17% (Sunny) -> ~59.2 pts
        jit_sunny = JitterResult(0.24, 3.17, 0.45, 0.07, 0.018, 10)
        score_sunny = calculate_cab_score(acc, inv, cont, jit_sunny, sln_base)
        self.assertTrue(58.0 < score_sunny.s_jitter < 61.0)

        # 2. Test ASLN Rational Curve: S_sln = 100 / (1 + (itr/18.0)^1.8)
        # Native immunity -> 100 pts
        score_sln_imm = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 0.0, 0.0, True, "S+", 10))
        self.assertEqual(score_sln_imm.s_short_ln, 100.0)

        # Brute-force LN-to-tap stripping -> 0 pts
        score_sln_brute = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 0.0, 0.0, False, "F", 10, is_brute_force_tap=True))
        self.assertEqual(score_sln_brute.s_short_ln, 0.0)

        # LN excluded by tester -> 0 pts
        score_sln_ex = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 0.0, 0.0, False, "F", 10, ln_excluded=True))
        self.assertEqual(score_sln_ex.s_short_ln, 0.0)

        # Realistic release overhead: ITR = 14.4% (Sunny) -> ~59.9 pts
        score_sln_sunny = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.59, 14.41, 0.88, False, "A", 10))
        self.assertTrue(58.0 < score_sln_sunny.s_short_ln < 62.0)

        # ITR = 18.0% -> exactly 50 pts
        score_sln_18 = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.7, 18.0, 1.0, False, "A", 10))
        self.assertAlmostEqual(score_sln_18.s_short_ln, 50.0, places=1)

        # Runaway inflation: ITR = 109.4% (Mixed) -> < 5 pts
        score_sln_runaway = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(2.5, 109.4, 3.5, False, "F", 10))
        self.assertTrue(score_sln_runaway.s_short_ln < 5.0)

    def test_subtier_weights_and_scoring(self):
        from cab.metrics.scoring import (
            WEIGHT_ACCURACY_SUBTIER,
            WEIGHT_SHORT_LN_SUBTIER,
            WEIGHT_SPEED_SUBTIER,
            WEIGHT_JITTER_SUBTIER,
        )
        from cab.metrics.calibration import CalibrationResult

        # 1. Verify weights keep the All-Range ratios (40:15:5:10) and sum to 1.00
        self.assertAlmostEqual(WEIGHT_ACCURACY_SUBTIER, 4 / 7, places=6)   # 40/70
        self.assertAlmostEqual(WEIGHT_SHORT_LN_SUBTIER, 3 / 14, places=6)  # 15/70
        self.assertAlmostEqual(WEIGHT_SPEED_SUBTIER, 1 / 14, places=6)     # 5/70
        self.assertAlmostEqual(WEIGHT_JITTER_SUBTIER, 1 / 7, places=6)     # 10/70
        # Ratio preservation: ACC : ASLN : Speed : AJ == 40 : 15 : 5 : 10
        self.assertAlmostEqual(WEIGHT_ACCURACY_SUBTIER / WEIGHT_SHORT_LN_SUBTIER, 40 / 15, places=9)
        self.assertAlmostEqual(WEIGHT_SHORT_LN_SUBTIER / WEIGHT_SPEED_SUBTIER, 15 / 5, places=9)
        self.assertAlmostEqual(WEIGHT_SPEED_SUBTIER / WEIGHT_JITTER_SUBTIER, 5 / 10, places=9)
        total_subtier = (
            WEIGHT_ACCURACY_SUBTIER
            + WEIGHT_SHORT_LN_SUBTIER
            + WEIGHT_SPEED_SUBTIER
            + WEIGHT_JITTER_SUBTIER
        )
        self.assertAlmostEqual(total_subtier, 1.00, places=5)

        # 2. Test subtier evaluation
        dummy_calib = CalibrationResult(is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.0, global_mae=0.0, spearman_rho=1.0, spearman_p=1e-5,
            kendall_tau=1.0, stratified_rmse={}, weighted_stratified_rmse=0.0,
            mean_wtd=0.0, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[10.0, 11.0]
        )
        jit = JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 10)  # 100 pts
        sln = ShortLNResult(0.0, 0.0, 0.0, True, "S+", 10)  # 100 pts
        spd = SpeedResult(0.05, 0.05, 20000.0, 100.0, 10)  # 100 pts

        subtier_score = calculate_cab_score(
            accuracy=acc,
            inversion=None,
            continuity=None,
            jitter=jit,
            short_ln=sln,
            speed=spd,
            is_subtier=True,
        )

        self.assertTrue(subtier_score.is_subtier)
        self.assertIsNone(subtier_score.s_inversion)
        self.assertIsNone(subtier_score.s_continuity)
        self.assertAlmostEqual(subtier_score.s_accuracy, 100.0, places=1)
        self.assertAlmostEqual(subtier_score.s_short_ln, 100.0, places=1)
        self.assertAlmostEqual(subtier_score.s_speed, 100.0, places=1)
        self.assertAlmostEqual(subtier_score.s_jitter, 100.0, places=1)
        self.assertAlmostEqual(subtier_score.total_score, 100.0, places=1)

    def test_subtier_out_of_domain(self):
        from cab.metrics.calibration import CalibrationResult

        dummy_calib = CalibrationResult(is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.0, global_mae=0.0, spearman_rho=0.0, spearman_p=1.0,
            kendall_tau=0.0, stratified_rmse={}, weighted_stratified_rmse=0.0,
            mean_wtd=0.0, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[]
        )
        out_score = calculate_cab_score(
            accuracy=acc,
            inversion=None,
            continuity=None,
            jitter=None,
            short_ln=None,
            speed=None,
            is_subtier=True,
            is_out_of_domain=True,
        )
        self.assertTrue(out_score.is_out_of_domain)
        self.assertEqual(out_score.total_score, 0.0)
        self.assertIsNone(out_score.dimension_scores["Ground-Truth Accuracy"])
        self.assertIsNone(out_score.dimension_scores["Anti-Rate-Inversion"])
        self.assertIsNone(out_score.dimension_scores["Local Continuity"])


class TestAccuracyScoreUsesBalancedRMSE(unittest.TestCase):
    """The ACC score must be driven by the pattern-balanced RMSE.

    Regression guard: the RMSE sub-score used to be computed from
    ``global_rmse`` (a sample-count-weighted aggregate), which let the pattern
    sample distribution silently dominate the score.  It must now use
    ``weighted_stratified_rmse`` with fixed, sample-count-independent weights.
    """

    @staticmethod
    def _acc(global_rmse: float, weighted_rmse: float, rho: float = 1.0) -> AccuracyResult:
        dummy = CalibrationResult(
            is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[]
        )
        return AccuracyResult(
            global_rmse=global_rmse,
            global_mae=global_rmse,
            spearman_rho=rho,
            spearman_p=1e-5,
            kendall_tau=rho,
            stratified_rmse={},
            weighted_stratified_rmse=weighted_rmse,
            mean_wtd=0.0,
            wtd_by_tier={},
            calibration=dummy,
            calibrated_predictions=[],
        )

    def _s_acc(self, acc: AccuracyResult) -> float:
        return calculate_cab_score(accuracy=acc, is_subtier=True).s_accuracy

    def test_score_tracks_weighted_not_global_rmse(self):
        # Same global RMSE, different balanced RMSE -> score MUST differ.
        lo = self._s_acc(self._acc(global_rmse=1.0, weighted_rmse=0.5))
        hi = self._s_acc(self._acc(global_rmse=1.0, weighted_rmse=1.5))
        self.assertLess(hi, lo)

        # And it must NOT react to global RMSE when weighted RMSE is held fixed.
        a = self._s_acc(self._acc(global_rmse=0.5, weighted_rmse=1.0))
        b = self._s_acc(self._acc(global_rmse=2.5, weighted_rmse=1.0))
        self.assertAlmostEqual(a, b, places=6)

    def test_perfect_accuracy_scores_full_marks(self):
        from cab.metrics.scoring import ACC_RMSE_BASELINE, ACC_RMSE_DECAY

        # A balanced RMSE at or below the baseline floors the penalty at zero.
        self.assertLessEqual(0.0, ACC_RMSE_BASELINE)
        s = self._s_acc(self._acc(global_rmse=0.0, weighted_rmse=ACC_RMSE_BASELINE))
        self.assertAlmostEqual(s, 100.0, places=6)

        # Monotonic decay with the documented coefficient (s_acc is rounded to 2dp).
        over = ACC_RMSE_BASELINE + 1.0
        s_over = self._s_acc(self._acc(global_rmse=0.0, weighted_rmse=over))
        self.assertAlmostEqual(s_over, 60.0 + 40.0 * math.exp(-ACC_RMSE_DECAY), places=2)


class TestPatternWeights(unittest.TestCase):
    """Guards on the per-pattern weights behind the balanced stratified RMSE.

    The weights are a single source of truth in ``cab.metrics.accuracy``; the
    off-line recompute tool imports them rather than restating them.  These
    tests pin the contract that the tool relies on.
    """

    def test_expected_weights_and_fallback(self):
        from cab.metrics.accuracy import PATTERN_WEIGHTS, DEFAULT_PATTERN_WEIGHT

        expected = {
            "jack": 0.20,
            "speed": 0.20,
            "stamina": 0.20,
            "tech": 0.20,
            "ln": 0.15,
            "course": 0.05,
            "unsigned": 0.05,
        }
        self.assertEqual(PATTERN_WEIGHTS, expected)
        # ``unsigned`` must not outrank a properly classified pattern.
        self.assertLessEqual(DEFAULT_PATTERN_WEIGHT, PATTERN_WEIGHTS["course"])
        self.assertLessEqual(PATTERN_WEIGHTS["unsigned"], PATTERN_WEIGHTS["course"])

    def test_recompute_tool_imports_the_same_table(self):
        """The recompute tool must *import* the weights, not restate them."""
        import tools.recompute_scores as rc
        from cab.metrics.accuracy import (
            PATTERN_WEIGHTS,
            DEFAULT_PATTERN_WEIGHT,
            balanced_stratified_rmse,
        )

        self.assertIs(rc.PATTERN_WEIGHTS, PATTERN_WEIGHTS)
        self.assertEqual(rc.DEFAULT_PATTERN_WEIGHT, DEFAULT_PATTERN_WEIGHT)
        self.assertIs(
            rc.recompute_weighted_stratified_rmse, balanced_stratified_rmse
        )

    def test_balanced_rmse_is_weight_normalised_mean(self):
        """It is a weighted mean of RMSEs, not of squared errors."""
        from cab.metrics.accuracy import balanced_stratified_rmse

        rmse = {"jack": 0.4, "ln": 1.5}
        # (0.20*0.4 + 0.15*1.5) / (0.20 + 0.15) == 0.305 / 0.35
        self.assertAlmostEqual(
            balanced_stratified_rmse(rmse), (0.20 * 0.4 + 0.15 * 1.5) / 0.35, places=12
        )
        # A true weighted RMS would differ; assert we are NOT doing that.
        rms = math.sqrt((0.20 * 0.4**2 + 0.15 * 1.5**2) / 0.35)
        self.assertNotAlmostEqual(balanced_stratified_rmse(rmse), rms, places=6)

    def test_balanced_rmse_ignores_sample_counts(self):
        """Sample counts must not influence the aggregate at all."""
        from cab.metrics.accuracy import balanced_stratified_rmse

        rmse = {"jack": 0.4, "ln": 1.5}
        self.assertEqual(
            balanced_stratified_rmse(rmse, {"jack": 1, "ln": 99999}),
            balanced_stratified_rmse(rmse, {"jack": 99999, "ln": 1}),
        )

    def test_balanced_rmse_edge_cases(self):
        from cab.metrics.accuracy import balanced_stratified_rmse

        self.assertIsNone(balanced_stratified_rmse({}))
        self.assertIsNone(balanced_stratified_rmse(None))  # type: ignore[arg-type]
        # An unlisted pattern falls back to the default weight, not zero.
        only_unknown = balanced_stratified_rmse({"future-pattern": 2.0})
        self.assertAlmostEqual(only_unknown, 2.0, places=12)


if __name__ == "__main__":
    unittest.main()

