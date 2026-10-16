import tempfile
from pathlib import Path
import unittest

from cab.metrics.accuracy import AccuracyResult
from cab.metrics.calibration import CalibrationResult
from cab.metrics.inversion import InversionResult
from cab.metrics.continuity import ContinuityResult
from cab.metrics.jitter_metric import JitterResult
from cab.metrics.short_ln_metric import ShortLNResult
from cab.metrics.speed_metric import SpeedResult
from cab.metrics.scoring import calculate_cab_score
from cab.runner.config import BenchConfig, TIER_ALL, TIER_NUMBER, TIER_GREEK, TIER_INHUMAN, TIER_DEFINITIONS
from cab.runner.suite import BenchmarkRunResult, MultiTierBenchmarkResult
from cab.report.markdown import generate_multi_tier_summary_report
from cab.report.json_export import export_multi_tier_to_json


class TestTierSlicingAndReports(unittest.TestCase):
    def test_multi_tier_report_with_out_of_domain(self):
        config = BenchConfig.create(mode="quick", min_expected=12.5)

        dummy_calib = CalibrationResult(is_ng=True, raw_anchors={}, pava_anchors={}, macro_alerts=[])
        acc_master = AccuracyResult(
            global_rmse=0.5, global_mae=0.4, spearman_rho=0.95, spearman_p=1e-5,
            kendall_tau=0.9, stratified_rmse={}, weighted_stratified_rmse=0.5,
            mean_wtd=0.5, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[13.0, 14.0, 16.0]
        )
        inv_master = InversionResult(
            inversion_frequency=0.01, auc_deficit=0.001, max_relative_drop=0.05,
            max_absolute_drop=0.2, inverted_maps_count=1, total_maps_evaluated=10,
            composite_inversion_index=0.01
        )
        cont_master = ContinuityResult(
            cliff_events_count=1, max_lipschitz=5.0, mean_lipschitz=2.0, smoothness_score=92.0
        )
        jit_master = JitterResult(0.1, 1.2, 0.2, 0.05, 0.005, 10)
        sln_master = ShortLNResult(0.2, 5.0, 0.3, False, "A", 10)
        spd_master = SpeedResult(0.5, 0.4, 2000.0, 85.0, 10)

        score_master = calculate_cab_score(
            accuracy=acc_master,
            inversion=inv_master,
            continuity=cont_master,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            is_subtier=False,
        )

        master_res = BenchmarkRunResult(
            algorithm_name="TestAlgo",
            scale_type="ng",
            config=config,
            accuracy=acc_master,
            inversion=inv_master,
            continuity=cont_master,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            score=score_master,
            rate_curves={},
            duration_seconds=5.0,
            tier_id=TIER_ALL,
            tier_name=TIER_DEFINITIONS[TIER_ALL].name,
            failed_beatmaps=[
                {"name": "broken_map.osu", "expected": "14.0", "pattern": "speed", "error": "Parser failed on NaN timing"}
            ],
        )

        # Number tier is out of domain
        acc_empty = AccuracyResult(
            global_rmse=0.0, global_mae=0.0, spearman_rho=0.0, spearman_p=1.0,
            kendall_tau=0.0, stratified_rmse={}, weighted_stratified_rmse=0.0,
            mean_wtd=0.0, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[]
        )
        score_num = calculate_cab_score(
            accuracy=acc_empty,
            is_subtier=True,
            is_out_of_domain=True,
        )
        num_res = BenchmarkRunResult(
            algorithm_name="TestAlgo",
            scale_type="ng",
            config=config,
            accuracy=acc_empty,
            inversion=None,
            continuity=None,
            jitter=JitterResult(0, 0, 0, 0, 0, 0),
            short_ln=ShortLNResult(0, 0, 0, False, "N/A", 0),
            speed=SpeedResult(0, 0, 0, 0, 0),
            score=score_num,
            rate_curves={},
            duration_seconds=5.0,
            tier_id=TIER_NUMBER,
            tier_name=TIER_DEFINITIONS[TIER_NUMBER].name,
        )

        # Greek tier is in domain: evaluates 12.5 - 15.5 across 4 dimensions
        acc_greek = AccuracyResult(
            global_rmse=0.45, global_mae=0.35, spearman_rho=0.96, spearman_p=1e-5,
            kendall_tau=0.9, stratified_rmse={}, weighted_stratified_rmse=0.45,
            mean_wtd=0.45, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[13.0, 14.0]
        )
        score_greek = calculate_cab_score(
            accuracy=acc_greek,
            inversion=None,
            continuity=None,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            is_subtier=True,
        )
        greek_res = BenchmarkRunResult(
            algorithm_name="TestAlgo",
            scale_type="ng",
            config=config,
            accuracy=acc_greek,
            inversion=None,
            continuity=None,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            score=score_greek,
            rate_curves={},
            duration_seconds=5.0,
            tier_id=TIER_GREEK,
            tier_name=TIER_DEFINITIONS[TIER_GREEK].name,
        )

        # Inhuman tier
        acc_inhuman = AccuracyResult(
            global_rmse=0.55, global_mae=0.45, spearman_rho=0.90, spearman_p=1e-4,
            kendall_tau=0.85, stratified_rmse={}, weighted_stratified_rmse=0.55,
            mean_wtd=0.55, wtd_by_tier={}, calibration=dummy_calib, calibrated_predictions=[16.0]
        )
        score_inhuman = calculate_cab_score(
            accuracy=acc_inhuman,
            inversion=None,
            continuity=None,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            is_subtier=True,
        )
        inhuman_res = BenchmarkRunResult(
            algorithm_name="TestAlgo",
            scale_type="ng",
            config=config,
            accuracy=acc_inhuman,
            inversion=None,
            continuity=None,
            jitter=jit_master,
            short_ln=sln_master,
            speed=spd_master,
            score=score_inhuman,
            rate_curves={},
            duration_seconds=5.0,
            tier_id=TIER_INHUMAN,
            tier_name=TIER_DEFINITIONS[TIER_INHUMAN].name,
        )

        multi_res = MultiTierBenchmarkResult(
            algorithm_name="TestAlgo",
            scale_type="ng",
            config=config,
            duration_seconds=5.0,
            tier_results={
                TIER_ALL: master_res,
                TIER_NUMBER: num_res,
                TIER_GREEK: greek_res,
                TIER_INHUMAN: inhuman_res,
            },
            master_result=master_res,
        )

        # Verify failed_beatmaps property forwarding
        self.assertEqual(len(multi_res.failed_beatmaps), 1)
        self.assertEqual(multi_res.failed_beatmaps[0]["name"], "broken_map.osu")

        # Verify report generation without errors
        with tempfile.TemporaryDirectory() as tmpdir:
            md_path = Path(tmpdir) / "test_report.md"
            generate_multi_tier_summary_report(multi_res, md_path)
            self.assertTrue(md_path.exists())
            content = md_path.read_text(encoding="utf-8")
            self.assertIn("四大难度档位横向对比总表", content)
            self.assertIn("N/A", content)
            self.assertIn("broken_map.osu", content)
            self.assertIn("谱面异常与自动排除记录", content)

            # Verify JSON export without errors
            json_path = Path(tmpdir) / "test_summary.json"
            export_multi_tier_to_json(multi_res, json_path)
            self.assertTrue(json_path.exists())
            import json
            with open(json_path, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            self.assertEqual(len(data["failed_beatmaps"]), 1)
            self.assertTrue(data["tiers"]["number"]["is_out_of_domain"])
            self.assertIsNone(data["tiers"]["greek"]["inversion"])
            self.assertIsNone(data["tiers"]["greek"]["continuity"])


if __name__ == "__main__":
    unittest.main()
