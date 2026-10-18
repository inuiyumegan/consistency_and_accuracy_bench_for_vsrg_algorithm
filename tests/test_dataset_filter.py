"""Unit tests for dataset filtering and domain scoping in CAB-VA."""

from pathlib import Path

import pytest
from cab.parser.bench_loader import BenchmarkDataset, BenchmarkSample
from cab.runner.config import (
    BenchConfig,
    FILTER_PRESETS,
    get_filter_preset,
    preset_excludes_pattern,
)
from cab.runner.suite import BenchmarkSuite
from cab.adapter.base import BaseAdapter


class _StubAdapter(BaseAdapter):
    """Minimal adapter for tests that never reach the calculation stage."""

    def __init__(self):
        super().__init__(name="stub")

    def calculate(self, target):  # pragma: no cover - never invoked here
        return 0.0


# The real benchmark dataset ships with the repo in samples/4k/. The guard below
# keeps the suite runnable in environments where the sample data has been
# stripped (e.g. a trimmed checkout): tests that exercise filtering against the
# real manifest are skipped when it is absent, so the pure-logic suite can still
# run.
#
# Only the chart total is pinned — tier and domain expectations are derived from
# each test's own predicate, so adding or removing charts only requires bumping
# this single number.
_EXPECTED_CHARTS = 855
_BENCH_CSV = Path("samples/4k/uuzs_bench.csv")
_needs_dataset = pytest.mark.skipif(
    not _BENCH_CSV.exists(),
    reason="requires samples/4k benchmark dataset",
)


@_needs_dataset
def test_dataset_pattern_filtering():
    ds = BenchmarkDataset.load_from_csv()
    initial_len = len(ds)
    assert initial_len == _EXPECTED_CHARTS

    # Whitelist
    whitelist_ds = ds.filter(patterns=["speed", "jack"])
    assert len(whitelist_ds) > 0
    assert len(whitelist_ds) < initial_len
    patterns = {s.pattern.lower() for s in whitelist_ds}
    assert patterns.issubset({"speed", "jack"})

    # Blacklist (Exclude LN)
    no_ln_ds = ds.filter(exclude_patterns=["ln"])
    assert len(no_ln_ds) < initial_len
    assert all(s.pattern.lower() != "ln" for s in no_ln_ds)


@_needs_dataset
def test_dataset_difficulty_filtering():
    ds = BenchmarkDataset.load_from_csv()

    # ND in [10.5, 18.5]
    diff_ds = ds.filter(min_expected=10.5, max_expected=18.5)
    assert len(diff_ds) > 0
    assert all(10.5 <= s.expected <= 18.5 for s in diff_ds)

    # Upper bound only
    low_ds = ds.filter(max_expected=5.0)
    assert len(low_ds) > 0
    assert all(s.expected <= 5.0 for s in low_ds)


@_needs_dataset
def test_dataset_name_and_combined_filter():
    ds = BenchmarkDataset.load_from_csv()

    name_ds = ds.filter(name_filter="darling")
    assert len(name_ds) > 0
    assert all("darling" in s.name.lower() for s in name_ds)

    # Combined: Daniel target domain. Cross-checked against the equivalent
    # predicate so the expectation cannot drift from the filter implementation.
    daniel_ds = ds.filter(
        exclude_patterns=["ln"],
        min_expected=10.5,
        max_expected=18.5,
    )
    manual = [
        s for s in ds if s.pattern.lower() != "ln" and 10.5 <= s.expected <= 18.5
    ]
    assert len(daniel_ds) == len(manual)
    assert 0 < len(daniel_ds) < len(ds)
    assert all(s.pattern.lower() != "ln" for s in daniel_ds)
    assert all(10.5 <= s.expected <= 18.5 for s in daniel_ds)

    stats = daniel_ds.get_stats_summary()
    assert stats["total"] == len(daniel_ds)
    assert "ln" not in stats["patterns"]
    assert stats["min_expected"] >= 10.5
    assert stats["max_expected"] <= 18.5


def test_bench_config_preset_daniel():
    cfg = BenchConfig.create(mode="quick", filter_preset="daniel")
    assert cfg.filter_preset == "daniel"
    assert cfg.exclude_patterns == ["ln"]
    assert cfg.min_expected == 10.5
    assert cfg.max_expected == 18.5
    assert cfg.has_filters() is True

    summary = cfg.get_filter_summary()
    assert "Preset: daniel" in summary
    assert "Exclude: ln" in summary
    assert "10.5, 18.5" in summary


def test_filter_preset_table_is_source_of_truth():
    """The preset table must drive both CLI choices and config expansion."""
    assert "daniel" in FILTER_PRESETS

    # Table lookup is case-insensitive and validates unknown names.
    assert get_filter_preset("Daniel") is FILTER_PRESETS["daniel"]
    with pytest.raises(ValueError, match="Unknown filter preset"):
        get_filter_preset("does-not-exist")

    # preset_excludes_pattern mirrors what the preset actually declares.
    assert preset_excludes_pattern("daniel", "ln") is True
    assert preset_excludes_pattern("daniel", "speed") is False

    # Every preset's declared keys must land on the config verbatim.
    for name, preset in FILTER_PRESETS.items():
        cfg = BenchConfig.create(mode="quick", filter_preset=name)
        for key in ("patterns", "exclude_patterns", "min_expected", "max_expected"):
            if key in preset:
                assert getattr(cfg, key) == preset[key], f"{name}.{key} mismatch"


def test_preset_explicit_args_override():
    """Explicit CLI kwargs must beat preset defaults."""
    cfg = BenchConfig.create(
        mode="quick",
        filter_preset="daniel",
        min_expected=12.0,
        max_expected=16.0,
    )
    assert cfg.min_expected == 12.0
    assert cfg.max_expected == 16.0
    # Un-overridden keys still come from the preset.
    assert cfg.exclude_patterns == ["ln"]


@_needs_dataset
def test_empty_filter_guard():
    cfg = BenchConfig.create(min_expected=999.0)  # No maps have ND >= 999
    # The guard fires while filtering, before any algorithm is invoked, so a
    # stub adapter is enough here — no dependency on test_algorithms/ sources.
    adapter = _StubAdapter()
    with pytest.raises(ValueError, match="Dataset is empty after applying filters"):
        BenchmarkSuite(adapter=adapter, config=cfg)


@_needs_dataset
def test_perturbation_samples_exclude_course_and_ln():
    ds = BenchmarkDataset.load_from_csv()
    pert_samples = ds.get_perturbation_samples()
    assert len(pert_samples) > 0
    assert all(not s.is_course for s in pert_samples)
    assert all(not s.is_ln for s in pert_samples)
    assert all(s.pattern.lower() not in {"course", "ln"} for s in pert_samples)
    # Check that accuracy samples still retain course and ln by default
    acc_samples = ds.get_accuracy_samples()
    assert any(s.is_course for s in acc_samples)
    assert any(s.is_ln for s in acc_samples)


@_needs_dataset
def test_multi_tier_dataset_slicing():
    ds = BenchmarkDataset.load_from_csv()
    assert len(ds) == _EXPECTED_CHARTS

    # Number Dan: [-2.5, 10.5)
    num_ds = ds.filter(min_expected=-2.5, max_expected=10.5, min_inclusive=True, max_inclusive=False)
    assert len(num_ds) > 0
    assert all(-2.5 <= s.expected < 10.5 for s in num_ds)

    # Greek Dan: [10.5, 15.5]
    grk_ds = ds.filter(min_expected=10.5, max_expected=15.5, min_inclusive=True, max_inclusive=True)
    assert len(grk_ds) > 0
    assert all(10.5 <= s.expected <= 15.5 for s in grk_ds)

    # Inhuman Dan: > 15.5
    inh_ds = ds.filter(min_expected=15.5, min_inclusive=False)
    assert len(inh_ds) > 0
    assert all(s.expected > 15.5 for s in inh_ds)

    # Each slice must match its own predicate...
    assert len(num_ds) == sum(1 for s in ds if -2.5 <= s.expected < 10.5)
    assert len(grk_ds) == sum(1 for s in ds if 10.5 <= s.expected <= 15.5)
    assert len(inh_ds) == sum(1 for s in ds if s.expected > 15.5)
    # ...and the three tiers must partition the whole dataset exactly.
    assert len(num_ds) + len(grk_ds) + len(inh_ds) == _EXPECTED_CHARTS


def test_bench_config_tiers():
    cfg_multi = BenchConfig.create(tier_mode="multi")
    tiers_multi = cfg_multi.get_tier_list()
    assert len(tiers_multi) == 4
    tier_ids = [t.tier_id for t in tiers_multi]
    assert tier_ids == ["all", "number", "greek", "inhuman"]

    cfg_greek = BenchConfig.create(tier_mode="greek")
    tiers_greek = cfg_greek.get_tier_list()
    assert len(tiers_greek) == 1
    assert tiers_greek[0].tier_id == "greek"
    assert tiers_greek[0].min_val == 10.5
    assert tiers_greek[0].max_val == 15.5
