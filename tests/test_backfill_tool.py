"""Unit tests for the domain-profile backfill tool (tools/backfill_domain_profile.py).

The tool rebuilds derived artifacts (JSON fields + Markdown sections) for result
directories that predate the domain-profiling feature, without re-running any
algorithm.  These tests cover the two pure helpers that do the text surgery, so
regressions in the regex/anchors are caught early.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

import backfill_domain_profile as bf  # noqa: E402


OLD_REPORT = """# Report

### 2.1 维度一：基准准确性
#### 分键型准确性明细 (Pattern-Stratified Breakdown, 包含 Course)
| 键型分类 (Pattern) | 样本数 (Samples) | 单键型 RMSE (ng) | 说明 |
| :--- | :---: | :---: | :--- |
| `course` | - | **1.0184** | 长曲马拉松 / 段位组曲 (Marathon) |
| `jack` | - | **0.5471** | 常规单曲键型 |
| `ln` | - | **2.9713** | 常规单曲键型 |
| `speed` | - | **0.5599** | 常规单曲键型 |

> [!WARNING]
> some unrelated warning
"""

RMSE = {"course": 1.0184, "jack": 0.5471, "ln": 2.9713, "speed": 0.5599}
COUNTS = {"course": 38, "jack": 219, "ln": 102, "speed": 176}


def test_rebuild_table_upgrades_columns_and_adds_profile():
    out = bf._rebuild_table_block(OLD_REPORT, RMSE, COUNTS, "TestAlgo")
    assert bf.TABLE_HEADER_NEW in out
    assert bf.TABLE_HEADER_OLD not in out
    assert bf.PROFILE_MARKER in out
    # Sample counts now populated, ratio + verdict columns present.
    assert "| `jack` | 219 | **0.5471** |" in out
    assert "擅长" in out or "不擅长" in out
    # Unrelated content below the table survives.
    assert "some unrelated warning" in out


def test_rebuild_is_idempotent():
    once = bf._rebuild_table_block(OLD_REPORT, RMSE, COUNTS, "TestAlgo")
    twice = bf._rebuild_table_block(once, RMSE, COUNTS, "TestAlgo")
    assert once == twice
    assert once.count(bf.PROFILE_MARKER) == 1


def test_rebuild_no_op_without_table():
    text = "# Report\n\nno pattern table here\n"
    assert bf._rebuild_table_block(text, RMSE, COUNTS, "TestAlgo") == text


def test_update_json_accuracy_adds_expected_fields():
    acc = {"stratified_rmse": dict(RMSE), "global_rmse": 1.219}
    bf._update_json_accuracy(acc, COUNTS)
    assert acc["stratified_counts"] == COUNTS
    dp = acc["domain_profile"]
    assert dp["baseline_rmse"] > 0
    assert dp["has_judged"] is True
    assert set(dp) >= {"strengths", "weaknesses", "adequate", "insufficient", "verdicts"}
    # Pre-existing values are untouched.
    assert acc["global_rmse"] == 1.219


def test_update_json_accuracy_empty_rmse_is_safe():
    acc = {"stratified_rmse": {}}
    bf._update_json_accuracy(acc, {})
    assert acc["stratified_counts"] == {}
    assert acc["domain_profile"]["has_judged"] is False


def test_counts_for_tier_reads_sample_stats():
    payload = {"dataset_filters": {"sample_stats": {"patterns": {"Jack": 10, "LN": 3}}}}
    assert bf._counts_for_tier(payload) == {"jack": 10, "ln": 3}


def test_counts_for_tier_missing_stats():
    assert bf._counts_for_tier({}) == {}
