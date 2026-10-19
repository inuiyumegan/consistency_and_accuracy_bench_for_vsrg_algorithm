import unittest
import math
import numpy as np
from cab.metrics.calibration import weighted_pava, ScaleCalibrator, CalibrationResult
from cab.metrics.inversion import evaluate_rate_inversion, InversionResult
from cab.metrics.continuity import evaluate_continuity, ContinuityResult
from cab.metrics.jitter_metric import evaluate_jitter, JitterResult
from cab.metrics.short_ln_metric import evaluate_short_ln, ShortLNResult
from cab.metrics.speed_metric import evaluate_speed, calculate_speed_score, SpeedResult
from cab.metrics.scoring import calculate_cab_score, BETA, BETA_SCALE
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
        calib_res = calibrator.calibrate(expected, predicted, is_nd_native=False)
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

    def test_rate_inversion_mesh_density_invariance(self):
        # Subdividing an inverted interval [1.1, 1.2] into 5 fine steps of 0.02
        # while keeping [1.0, 1.1] at coarse step 0.10 must preserve IF, IDA, and max_relative_drop.
        coarse = {
            "clean": [(1.0, 10.0), (1.1, 11.0), (1.2, 12.0)],
            "inv": [(1.0, 8.0), (1.1, 10.0), (1.2, 9.0)],
        }
        refined = {
            "clean": [(1.0, 10.0), (1.1, 11.0), (1.2, 12.0)],
            "inv": [
                (1.0, 8.0),
                (1.1, 10.0),
                (1.12, 9.8),
                (1.14, 9.6),
                (1.16, 9.4),
                (1.18, 9.2),
                (1.2, 9.0),
            ],
        }
        res_coarse = evaluate_rate_inversion(coarse)
        res_refined = evaluate_rate_inversion(refined)
        self.assertAlmostEqual(res_coarse.inversion_frequency, res_refined.inversion_frequency, places=6)
        self.assertAlmostEqual(res_coarse.auc_deficit, res_refined.auc_deficit, places=6)
        self.assertAlmostEqual(res_coarse.max_relative_drop, res_refined.max_relative_drop, places=6)
        self.assertAlmostEqual(res_coarse.max_absolute_drop, res_refined.max_absolute_drop, places=6)
        self.assertEqual(res_refined.worst_maps[0].worst_transition, (1.1, 1.2, 10.0, 9.0))

    def test_rate_inversion_negative_and_near_zero_scales(self):
        # Negative ND scale (e.g. Beginner maps in [-2.0, -1.0]) and near-zero positive scale
        curves = {
            "neg_nd": [(1.0, -2.0), (1.1, -1.0), (1.2, -1.5)],
            "near_zero": [(1.0, 0.05), (1.1, 0.20), (1.2, 0.10)],
        }
        res = evaluate_rate_inversion(curves)
        self.assertEqual(res.inverted_maps_count, 2)
        self.assertAlmostEqual(res.inversion_frequency, 0.5, places=6)
        # On neg_nd: M= -1.0, d_min= -2.0 -> denom = max(-1.0 - (-2.0), 1.0) = 1.0 -> drop 0.5/1.0 = 0.5
        # On near_zero: M= 0.20, d_min= 0.05 -> denom = max(0.20, 1.0) = 1.0 -> drop 0.10/1.0 = 0.10
        self.assertAlmostEqual(res.max_relative_drop, 0.5, places=6)

    def test_rate_inversion_micro_deadband(self):
        # Sub-deadband rounding jitter (< 1e-4 relative / < 5e-4 absolute) is ignored
        curves = {
            "jitter_only": [(1.0, 10.0), (1.1, 10.0000), (1.12, 9.9998), (1.2, 10.5)],
        }
        res = evaluate_rate_inversion(curves)
        self.assertEqual(res.inverted_maps_count, 0)
        self.assertEqual(res.inversion_frequency, 0.0)
        self.assertEqual(res.auc_deficit, 0.0)
        self.assertEqual(res.max_relative_drop, 0.0)

    def test_continuity_scale_invariance_and_quadratic_mesh_invariance(self):
        # 1) A smooth quadratic curve D(r) = c * r^2 sampled on a non-uniform mesh
        #    (coarse 0.10 steps mixed with fine 0.02 steps) has zero kink residual
        #    and identical smoothness_score whether c = 2.0 (Star scale) or c = 25.0 (MSD scale).
        rates = [1.0, 1.1, 1.12, 1.14, 1.16, 1.18, 1.2, 1.3, 1.4, 1.5]
        star_curve = {"m1": [(r, 2.0 * (r ** 2)) for r in rates]}
        msd_curve = {"m1": [(r, 25.0 * (r ** 2)) for r in rates]}

        res_star = evaluate_continuity(star_curve)
        res_msd = evaluate_continuity(msd_curve)

        self.assertEqual(res_star.cliff_events_count, 0)
        self.assertEqual(res_msd.cliff_events_count, 0)
        self.assertAlmostEqual(res_star.mean_roughness, 0.0, places=9)
        self.assertAlmostEqual(res_msd.mean_roughness, 0.0, places=9)
        self.assertEqual(res_star.smoothness_score, 100.0)
        self.assertEqual(res_msd.smoothness_score, 100.0)

    def test_continuity_detects_cliff_on_low_scale(self):
        # On a 2-star curve, a +35% jump (2.2 -> 2.97) in a 0.02 step has raw
        # gradient 38.5 (< 50), which the old Samuelson-locked detector missed completely.
        curve = {
            "low_star_cliff": [
                (1.00, 2.00),
                (1.02, 2.04),
                (1.04, 2.08),
                (1.06, 2.12),
                (1.08, 2.97),  # Sudden cliff!
                (1.10, 3.01),
                (1.12, 3.05),
            ]
        }
        res = evaluate_continuity(curve)
        self.assertGreaterEqual(res.cliff_events_count, 1)
        self.assertLess(res.smoothness_score, 50.0)

    def test_short_ln_zero_response_is_under_reaction(self):
        # A zero-response probe means the algorithm never registered the
        # injected short LNs. Under the two-sided tolerance band that reads as
        # under-reaction (≈7 points below the realistic target), NOT immunity.
        orig = [3.0, 4.0, 5.0]
        sln = [3.0, 4.0, 5.0]
        res = evaluate_short_ln(orig, sln)
        self.assertTrue(res.has_native_immunity)  # informational: output did not move
        self.assertAlmostEqual(res.score, 55.98, places=2)
        self.assertIn("Moderate Deviation", res.immunity_grade)

    def test_short_ln_brute_force_and_exclusion(self):
        orig = [3.0, 4.0, 5.0]
        sln = [3.0, 4.0, 5.0]  # numeric delta is 0

        # Brute-force LN-to-tap stripping is gated to 0 by the continuous HO
        # capability probe (LN-blind), not merely placed on the band.
        res_brute = evaluate_short_ln(orig, sln, is_brute_force_tap=True, ho_delta_pct=0.0)
        self.assertTrue(res_brute.is_brute_force_tap)
        self.assertAlmostEqual(res_brute.score, 0.0, places=2)

        # A tester-side LN exclusion leaves nothing to measure -> 0 and F.
        res_ex = evaluate_short_ln(orig, sln, ln_excluded=True)
        self.assertEqual(res_ex.score, 0.0)
        self.assertIn("LN Excluded", res_ex.immunity_grade)

    def test_speed_metric_scoring(self):
        # <= 0.015 ms/s should be 100 pts
        self.assertEqual(calculate_speed_score(0.005), 100.0)
        self.assertEqual(calculate_speed_score(0.015), 100.0)
        # 1.00 ms/s should be 75.0 pts
        self.assertEqual(calculate_speed_score(0.15), 75.0)
        # 10.00 ms/s should be 50.0 pts
        self.assertEqual(calculate_speed_score(1.50), 50.0)
        # 100.00 ms/s should be 25.0 pts
        self.assertEqual(calculate_speed_score(15.00), 25.0)
        # >= 1000.00 ms/s should be 0.0 pts
        self.assertEqual(calculate_speed_score(150.00), 0.0)
        self.assertEqual(calculate_speed_score(2500.00), 0.0)

        # evaluate_speed across samples
        timings = [
            (100.0, 100.0),  # 1.0 ms/s
            (50.0, 100.0),   # 0.5 ms/s
        ]
        s_res = evaluate_speed(timings)
        self.assertAlmostEqual(s_res.average_speed_ms_per_s, 0.75, places=2)
        # median 0.75 ms/s -> 100 - 25*log10(0.75/0.015) = 57.5
        self.assertAlmostEqual(s_res.speed_score, 57.5, places=1)

    def test_speed_overhead_subtraction(self):
        """Compatibility-layer start-up must not be charged to the algorithm.

        Dan-Overlay's msd.exe goes through Wine on Linux, where one invocation
        costs ~5 s of process start-up against ~11 ms of real MinaCalc work.
        Subtracting the measured overhead makes the reading comparable with a
        native run.
        """
        timings = [
            (5000.0, 500.0),  # 10.0 ms/s raw
            (5200.0, 500.0),  # 10.4 ms/s raw
        ]
        raw = evaluate_speed(timings)
        self.assertEqual(raw.platform_overhead_ms, 0.0)

        # Subtract 4900 ms -> (100 / 500) = 0.2 and (300 / 500) = 0.6
        adj = evaluate_speed(timings, platform_overhead_ms=4900.0)
        self.assertAlmostEqual(adj.platform_overhead_ms, 4900.0, places=3)
        self.assertAlmostEqual(adj.average_speed_ms_per_s, 0.4, places=4)
        self.assertGreater(adj.speed_score, raw.speed_score)

    def test_speed_overhead_ignores_samples_below_overhead(self):
        """A sample shorter than the overhead never paid it (cache hit / native),
        so it must be left untouched rather than driven negative."""
        adj = evaluate_speed([(200.0, 500.0)], platform_overhead_ms=1000.0)
        self.assertAlmostEqual(adj.median_speed_ms_per_s, 0.4, places=4)

    def test_beta_ordering_and_multiplicative_composite(self):
        # The composite is ACC x PROD(1 - beta*(1-s/100)); there are no weights.
        # beta ordering: AIN == ASLN > CT > Speed == AJ
        self.assertGreater(BETA["Anti-Rate-Inversion"], BETA["Local Continuity"])
        self.assertEqual(BETA["Anti-Rate-Inversion"], BETA["Anti-Short LN"])
        self.assertGreater(BETA["Anti-Short LN"], BETA["Local Continuity"])
        self.assertGreater(BETA["Local Continuity"], BETA["Computation Speed"])
        self.assertEqual(BETA["Computation Speed"], BETA["Anti-Jitter"])
        # beta = base * BETA_SCALE, base = 0.30/0.30/0.20/0.10/0.10, scale = 0.5
        self.assertAlmostEqual(BETA_SCALE, 0.5, places=6)
        self.assertAlmostEqual(BETA["Anti-Rate-Inversion"], 0.15, places=6)
        self.assertAlmostEqual(BETA["Anti-Short LN"], 0.15, places=6)
        self.assertAlmostEqual(BETA["Local Continuity"], 0.10, places=6)
        self.assertAlmostEqual(BETA["Computation Speed"], 0.05, places=6)
        self.assertAlmostEqual(BETA["Anti-Jitter"], 0.05, places=6)

    def test_perfect_dimensions_do_not_inflate_the_total(self):
        # Every robustness dimension at 100 -> discount 1.0 -> total == ACC.
        # This is the property the old weighted sum lacked: a saturated
        # dimension used to add points on top of ACC.
        from cab.metrics.calibration import CalibrationResult
        calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.5, global_mae=0.4, spearman_rho=0.95, spearman_p=1e-5,
            kendall_tau=0.9, stratified_rmse={}, weighted_stratified_rmse=0.5,
            mean_wtd=0.5, wtd_by_tier={}, calibration=calib,
        )
        perfect = calculate_cab_score(
            accuracy=acc,
            inversion=InversionResult(0.0, 0.0, 0.0, 0.0, 0, 10, 0.0),
            continuity=ContinuityResult(0, 5.0, 2.0, 100.0),
            jitter=JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 10),
            short_ln=ShortLNResult(0.0, 7.0, 0.0, False, "S", 10),
            speed=SpeedResult(0.05, 0.05, 20000.0, 100.0, 10),
        )
        self.assertAlmostEqual(perfect.total_score, perfect.s_accuracy, places=2)
        self.assertTrue(all(v == 1.0 for v in perfect.dimension_discounts.values()))

    def test_multiplicative_discount_is_non_compensatory(self):
        # A strong ASLN cannot buy back a failed AIN: the product only shrinks.
        from cab.metrics.calibration import CalibrationResult
        calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.5, global_mae=0.4, spearman_rho=0.95, spearman_p=1e-5,
            kendall_tau=0.9, stratified_rmse={}, weighted_stratified_rmse=0.5,
            mean_wtd=0.5, wtd_by_tier={}, calibration=calib,
        )
        bad_ain = calculate_cab_score(
            accuracy=acc,
            inversion=InversionResult(0.5, 0.5, 0.0, 0.0, 0, 10, 0.0),  # ~0 pts
            continuity=ContinuityResult(0, 5.0, 2.0, 100.0),
            jitter=JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 10),
            short_ln=ShortLNResult(0.0, 7.0, 0.0, False, "S", 10),
            speed=SpeedResult(0.05, 0.05, 20000.0, 100.0, 10),
        )
        self.assertLess(bad_ain.total_score, bad_ain.s_accuracy)
        self.assertAlmostEqual(
            bad_ain.total_score,
            bad_ain.s_accuracy * (1 - BETA["Anti-Rate-Inversion"] * (1 - bad_ain.s_inversion / 100)),
            places=1,
        )

    def test_anti_jitter_and_short_ln_rational_curves(self):
        from cab.metrics.calibration import CalibrationResult
        dummy_calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
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

        # 2. Test two-sided ASLN band: S = 100 / (1 + (|itr - 7|/8)^1.8)
        # On target (ITR == 7%) -> 100 pts
        score_sln_on = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 7.0, 0.0, False, "S", 10))
        self.assertAlmostEqual(score_sln_on.s_short_ln, 100.0, places=1)

        # Band is symmetric: +14.41 and -0.41 are equidistant from 7
        score_sln_hi = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.59, 14.41, 0.88, False, "B", 10))
        score_sln_lo = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.59, -0.41, 0.88, False, "B", 10))
        self.assertAlmostEqual(score_sln_hi.s_short_ln, 53.44, places=1)
        self.assertAlmostEqual(score_sln_lo.s_short_ln, 53.44, places=1)

        # Zero response is under-reaction, not immunity -> mid-band, not 100
        score_sln_zero = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 0.0, 0.0, True, "B", 10))
        self.assertAlmostEqual(score_sln_zero.s_short_ln, 55.98, places=1)

        # LN-blind (HO delta 0%) is smoothly gated to 0; partial/full models keep credit
        score_sln_brute = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 7.0, 0.0, False, "B", 10, is_brute_force_tap=True, ho_delta_pct=0.0))
        self.assertAlmostEqual(score_sln_brute.s_short_ln, 0.0, places=1)
        score_sln_full = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 7.0, 0.0, False, "S", 10, ho_delta_pct=20.0))
        self.assertGreater(score_sln_full.s_short_ln, 95.0)

        # AJ arrangement gate: density-blind (1.5%) crushed, arrangement-aware (18%) ~intact
        blind = calculate_cab_score(acc, inv, cont, JitterResult(0, 0.8, 0, 0, 0, 10, 1.5, True), sln_base)
        aware = calculate_cab_score(acc, inv, cont, JitterResult(0, 0.8, 0, 0, 0, 10, 18.0, False), sln_base)
        # The arrangement gate is square-rooted (2026-10-19) so a density-blind
        # run lands near 24 instead of ~6 -- still a hard discount against ~99.
        self.assertLess(blind.s_jitter, 35.0)
        self.assertGreater(aware.s_jitter, 88.0)
        self.assertLess(blind.s_jitter, aware.s_jitter * 0.35)

        # LN excluded by tester -> nothing measured -> 0 pts
        score_sln_ex = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(0.0, 0.0, 0.0, False, "F", 10, ln_excluded=True))
        self.assertEqual(score_sln_ex.s_short_ln, 0.0)

        # Severe over-inflation still collapses: ITR = 109.4% -> ~1 pt
        score_sln_runaway = calculate_cab_score(acc, inv, cont, jit_0, ShortLNResult(2.5, 109.4, 3.5, False, "F", 10))
        self.assertLess(score_sln_runaway.s_short_ln, 1.1)

    def test_subtier_scoring(self):
        from cab.metrics.calibration import CalibrationResult

        # 1. Sub-tiers only drop AIN and CT from the product; no weight
        #    renormalisation is needed because discounts are multiplicative.
        dummy_calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc = AccuracyResult(
            global_rmse=0.0, global_mae=0.0, spearman_rho=1.0, spearman_p=1e-5,
            kendall_tau=1.0, stratified_rmse={}, weighted_stratified_rmse=0.0,
            mean_wtd=0.0, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[10.0, 11.0]
        )
        jit = JitterResult(0.0, 0.0, 0.0, 0.0, 0.0, 10)  # 100 pts
        sln = ShortLNResult(0.0, 7.0, 0.0, False, "S", 10)  # 100 pts (on target)
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

        dummy_calib = CalibrationResult(is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
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
            is_nd=True, raw_anchors={}, pava_anchors={}, macro_alerts=[]
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

    def test_macro_inversion_alerts_are_diagnostic_only(self):
        """The stored alert list itself never moves the score.

        v2 of the structural-inversion deduction (reintroduced 2026-10-17 as the
        ACC-side penalty) is derived from the cohort anchors via
        ``macro_inversion_severity`` — not from this alert list, which remains
        display/diagnostic material.  Anchors are identical here, so alerts must
        not change the ACC score.
        """
        from cab.metrics.calibration import MacroInversionAlert

        alert = MacroInversionAlert(
            lower_tier=1.0,
            higher_tier=2.0,
            lower_anchor=1.0,
            higher_anchor=0.9,
            violation_amount=0.1,
        )
        alerted = self._acc(global_rmse=1.0, weighted_rmse=1.0)
        alerted.calibration = CalibrationResult(
            is_nd=False,
            raw_anchors={},
            pava_anchors={},
            macro_alerts=[alert, alert, alert, alert],
        )
        plain = self._acc(global_rmse=1.0, weighted_rmse=1.0)
        self.assertEqual(self._s_acc(alerted), self._s_acc(plain))

    def test_macro_inversion_penalty_scales_with_anchor_disorder(self):
        """v2 ACC penalty: monotone anchors -> 0; inverted anchors -> SCALE*S."""
        from cab.metrics.macro_inversion import macro_inversion_severity
        from cab.metrics.scoring import (
            MACRO_INVERSION_PENALTY_CAP,
            MACRO_INVERSION_PENALTY_SCALE,
        )

        # 1. Monotone anchors: severity 0, penalty 0.
        mono = self._acc(global_rmse=1.0, weighted_rmse=1.0)
        mono.calibration = CalibrationResult(
            is_nd=False,
            raw_anchors={1.0: 1.0, 2.0: 2.0, 3.0: 3.0, 4.0: 4.0, 5.0: 5.0},
            pava_anchors={},
            macro_alerts=[],
        )
        plain = self._acc(global_rmse=1.0, weighted_rmse=1.0)
        self.assertEqual(self._s_acc(mono), self._s_acc(plain))

        # 2. Inverted anchors: severity must bite into the ACC score.
        anchors = {1.0: 5.0, 2.0: 4.0, 3.0: 3.0, 4.0: 2.0, 5.0: 1.0}
        disordered = self._acc(global_rmse=1.0, weighted_rmse=1.0)
        disordered.calibration = CalibrationResult(
            is_nd=False, raw_anchors=anchors, pava_anchors={}, macro_alerts=[]
        )
        severity = macro_inversion_severity(anchors)
        expected_penalty = min(
            MACRO_INVERSION_PENALTY_CAP, MACRO_INVERSION_PENALTY_SCALE * severity
        )
        self.assertGreater(severity, 0.0)
        self.assertGreater(expected_penalty, 0.0)
        self.assertAlmostEqual(
            self._s_acc(disordered),
            self._s_acc(plain) - expected_penalty,
            places=1,
        )

    def test_macro_inversion_penalty_covers_nd_native_calibration(self):
        """nd-native calibration keeps an identity mapping but still records
        anchors + alerts, so the inversion penalty applies to it as well."""
        import numpy as np

        from cab.metrics.calibration import ScaleCalibrator
        from cab.metrics.macro_inversion import macro_inversion_severity

        expected = np.array([1.0, 1.0, 2.0, 2.0, 3.0, 3.0])
        predicted = np.array([3.0, 3.2, 2.0, 1.8, 1.0, 1.2])  # strongly inverted
        calib = ScaleCalibrator().calibrate(expected, predicted, is_nd_native=True)

        self.assertTrue(calib.is_nd)
        self.assertTrue(calib.raw_anchors)          # previously returned empty
        self.assertEqual(len(calib.macro_alerts), 2)
        self.assertGreater(macro_inversion_severity(calib.raw_anchors), 0.0)


class TestPatternWeights(unittest.TestCase):
    """Guards on the per-pattern weights behind the balanced stratified RMSE.

    The weights are a single source of truth in ``cab.metrics.accuracy``; the
    off-line recompute tool imports them rather than restating them.  These
    tests pin the contract that the tool relies on.

    The cross-repo half of this contract (that the tool really imports the
    table instead of restating it) lives with the tool, in the selfcheck repo.
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


class TestSubPatternBalancing(unittest.TestCase):
    """Guards on the 2026-10-18 sub-pattern weighting (family weight x 3:1 split).

    Family weights stay untouched; inside a family every classified sub-pattern
    counts 3 and the ``Unsigned`` catch-all counts 1, re-normalised over the
    buckets that are actually present.
    """

    def test_ratios_and_alias(self):
        from cab.metrics.accuracy import (
            binomial_sub_pattern,
            normalize_sub_pattern,
            sub_pattern_ratio,
        )

        self.assertEqual(normalize_sub_pattern("jacky tech"), "jack tech")
        self.assertEqual(sub_pattern_ratio("jacky tech"), 3.0)
        self.assertEqual(sub_pattern_ratio("stream tech"), 3.0)
        self.assertEqual(sub_pattern_ratio("Unsigned"), 1.0)
        self.assertEqual(binomial_sub_pattern("tech", "Unsigned"), "tech-Unsigned")

    def test_tech_family_matches_declared_split(self):
        """tech: stream : jack : mix : unsigned = 3 : 3 : 3 : 1."""
        from cab.metrics.accuracy import balanced_subpattern_rmse

        subs = {
            "tech": {
                "stream tech": 0.5,
                "jack tech": 0.5,
                "mix tech": 0.5,
                "Unsigned": 2.0,
            }
        }
        # (3*0.5 + 3*0.5 + 3*0.5 + 1*2.0) / (3+3+3+1) = 6.5 / 10
        self.assertAlmostEqual(balanced_subpattern_rmse(subs), 0.65, places=12)

    def test_family_weights_are_preserved(self):
        """Each family still contributes exactly its own weight."""
        from cab.metrics.accuracy import balanced_subpattern_rmse

        subs = {
            "jack": {"high chordjack": 1.0, "low chordjack": 1.0},
            "ln": {"release": 2.0},
        }
        # jack -> 1.0 (weight .20), ln -> 2.0 (weight .15)
        self.assertAlmostEqual(
            balanced_subpattern_rmse(subs), (0.20 * 1.0 + 0.15 * 2.0) / 0.35, places=12
        )

    def test_missing_buckets_renormalise_within_family(self):
        from cab.metrics.accuracy import balanced_subpattern_rmse

        subs = {"tech": {"stream tech": 1.0, "Unsigned": 2.0}}
        # (3*1 + 1*2) / 4 = 1.25; the family still carries its full .20 weight.
        self.assertAlmostEqual(balanced_subpattern_rmse(subs), 1.25, places=12)

    def test_edge_cases(self):
        from cab.metrics.accuracy import balanced_subpattern_rmse

        self.assertIsNone(balanced_subpattern_rmse({}))
        self.assertIsNone(balanced_subpattern_rmse(None))  # type: ignore[arg-type]
        self.assertIsNone(balanced_subpattern_rmse({"tech": {}}))

    def test_sample_counts_do_not_matter(self):
        from cab.metrics.accuracy import balanced_subpattern_rmse

        subs = {"jack": {"high chordjack": 0.4, "low chordjack": 1.2}}
        counts_a = {"jack": {"high chordjack": 1, "low chordjack": 9999}}
        counts_b = {"jack": {"high chordjack": 9999, "low chordjack": 1}}
        self.assertEqual(
            balanced_subpattern_rmse(subs, counts_a),
            balanced_subpattern_rmse(subs, counts_b),
        )


if __name__ == "__main__":
    unittest.main()

