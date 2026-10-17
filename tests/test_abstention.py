"""Unit tests for the declared difficulty-scope (abstention) module.

A declaration never alters the headline metrics -- it only marks beatmaps as
out of scope for the secondary in-scope reading. These tests pin the parsing
grammar, the in-scope / out-of-scope split, the usage-coverage estimate, and
the human-readable descriptions.
"""

from pathlib import Path

import pytest

from cab.metrics.abstention import (
    AbstentionDeclaration,
    ScopeReport,
    apply_declarations,
    describe_scope,
    estimate_out_of_scope_usage,
    parse_declaration,
    sample_in_scope,
)
from cab.parser.bench_loader import BenchmarkSample


def _sample(name, pattern, expected, sub="stream tech"):
    return BenchmarkSample(
        bid=None,
        name=name,
        pattern=pattern,
        sub_pattern=sub,
        expected=expected,
        file_path=Path(f"/repo/samples/4k/{pattern}/{name}.osu"),
    )


class TestDeclarationParsing:
    def test_pattern_single(self):
        d = parse_declaration("pattern:ln")
        assert d.patterns == ("ln",)
        assert d.min_nd is None and d.max_nd is None

    def test_pattern_list_deduped(self):
        d = parse_declaration("pattern:ln,tech,ln")
        assert d.patterns == ("ln", "tech")

    def test_pattern_pipe_separator(self):
        assert parse_declaration("pattern:ln|tech").patterns == ("ln", "tech")

    def test_lower_bound_terms(self):
        # "<" and "<=" behave identically: the kept window is closed at the bound.
        assert parse_declaration("nd<10.5").min_nd == 10.5
        assert parse_declaration("nd<=10.5").min_nd == 10.5

    def test_upper_bound_terms(self):
        assert parse_declaration("nd>15.5").max_nd == 15.5
        assert parse_declaration("nd>=15.5").max_nd == 15.5

    def test_alias_difficulty(self):
        assert parse_declaration("difficulty<10.5").min_nd == 10.5
        assert parse_declaration("difficulty>=15.5").max_nd == 15.5

    def test_combined_terms(self):
        d = parse_declaration("pattern:ln nd<10.5 nd>15.5")
        assert d.patterns == ("ln",)
        assert d.min_nd == 10.5
        assert d.max_nd == 15.5

    def test_repeated_bounds_tighten_the_window(self):
        assert parse_declaration("nd<10.5 nd<11.0").min_nd == 11.0
        assert parse_declaration("nd>15.5 nd>14.0").max_nd == 14.0

    def test_rejects_empty(self):
        with pytest.raises(ValueError):
            parse_declaration("   ")

    def test_rejects_unknown_pattern(self):
        with pytest.raises(ValueError):
            parse_declaration("pattern:stream")

    def test_rejects_unknown_key(self):
        with pytest.raises(ValueError):
            parse_declaration("bpm:180")

    def test_rejects_non_numeric_bound(self):
        with pytest.raises(ValueError):
            parse_declaration("nd<abc")

    def test_rejects_unrecognised_term(self):
        with pytest.raises(ValueError):
            parse_declaration("hello")


class TestScopeSplit:
    def test_no_declaration_keeps_everything(self):
        samples = [_sample("A", "jack", 9.0), _sample("B", "ln", 12.0)]
        report = apply_declarations(samples, [])
        assert not report.is_active
        assert len(report.in_scope_samples) == 2
        assert report.out_of_scope_samples == []

    def test_pattern_exclusion(self):
        samples = [_sample("A", "jack", 9.0), _sample("B", "ln", 12.0)]
        report = apply_declarations(samples, [parse_declaration("pattern:ln")])
        assert [s.name for s in report.in_scope_samples] == ["A"]
        assert [s.name for s in report.out_of_scope_samples] == ["B"]

    def test_boundary_value_is_kept(self):
        # The window is closed: ND == bound stays in scope.
        at = _sample("At", "speed", 10.5)
        below = _sample("Below", "speed", 10.4)
        report = apply_declarations([at, below], [parse_declaration("nd<10.5")])
        assert [s.name for s in report.in_scope_samples] == ["At"]
        assert [s.name for s in report.out_of_scope_samples] == ["Below"]

    def test_multiple_declarations_combine_with_or(self):
        samples = [
            _sample("ln1", "ln", 12.0),
            _sample("low", "speed", 5.0),
            _sample("keep", "speed", 12.0),
        ]
        report = apply_declarations(
            samples,
            [parse_declaration("pattern:ln"), parse_declaration("nd<10.5")],
        )
        assert sorted(s.name for s in report.in_scope_samples) == ["keep"]
        assert sorted(s.name for s in report.out_of_scope_samples) == ["ln1", "low"]

    def test_sample_in_scope_helper(self):
        d = parse_declaration("pattern:ln")
        assert not sample_in_scope(_sample("X", "ln", 12.0), [d])
        assert sample_in_scope(_sample("X", "tech", 12.0), [d])


class TestUsageEstimate:
    def test_no_declaration_gives_up_nothing(self):
        assert estimate_out_of_scope_usage([]) == 0.0

    def test_ln_family_share(self):
        # Dropping the LN family gives up ~31% of analyses once SV is removed
        # from the base: ln-family 0.292 / (rc 0.661 + ln-family 0.292).
        usage = estimate_out_of_scope_usage([parse_declaration("pattern:ln")])
        assert 0.29 < usage < 0.32

    def test_range_declaration_gives_up_some_usage(self):
        usage = estimate_out_of_scope_usage([parse_declaration("nd<10.5")])
        # ~56.9% of numeric-reading analyses sit below ND 10.5.
        assert 0.50 < usage < 0.62

    def test_always_within_unit_interval(self):
        combos = [
            "pattern:ln nd<10.5 nd>15.5",
            "nd<9.0",
            "nd>17.0",
            "pattern:ln,tech,course,jack,speed,stamina,unsigned",
        ]
        for spec in combos:
            usage = estimate_out_of_scope_usage([parse_declaration(spec)])
            assert 0.0 <= usage <= 1.0


class TestDescriptions:
    def test_describe_scope_full_dataset(self):
        assert describe_scope([]) == "Full dataset"

    def test_describe_scope_combined(self):
        text = describe_scope([parse_declaration("pattern:ln nd<10.5")])
        assert text == "4K, excluding LN, ND >= 10.5"

    def test_declaration_describe_raw(self):
        d = parse_declaration("pattern:ln nd>15.5")
        assert d.describe() == "skip=ln skip ND>15.5"

    def test_scope_report_describe(self):
        samples = [_sample("A", "ln", 5.0), _sample("B", "jack", 12.0)]
        report = apply_declarations(samples, [parse_declaration("pattern:ln")])
        text = report.describe()
        assert "out of scope 1/2 charts" in text

    def test_scope_report_full(self):
        report = apply_declarations([_sample("A", "jack", 9.0)], [])
        assert report.describe() == "Full dataset (no declared scope)."


class TestSecondaryReading:
    """The suite-level helper that fits a second accuracy pass on kept charts."""

    def _make_suite(self, declarations):
        from cab.runner.config import BenchConfig
        from cab.runner.suite import BenchmarkSuite

        suite = BenchmarkSuite.__new__(BenchmarkSuite)
        suite.config = BenchConfig.create(
            mode="quick",
            scope_declarations=tuple(declarations),
        )

        class _Adapter:
            scale_type = "nd"

        suite.adapter = _Adapter()
        return suite

    def test_no_declaration_returns_empty(self):
        suite = self._make_suite([])
        report, acc, preds = suite._evaluate_scope_accuracy(
            [_sample("A", "speed", 12.0)], [12.0]
        )
        assert report is None and acc is None and preds == []

    def test_in_scope_reading_uses_only_kept_charts(self):
        suite = self._make_suite([parse_declaration("nd<10.5")])
        samples = [
            _sample("low", "speed", 5.0),
            _sample("mid", "speed", 12.0),
            _sample("high", "speed", 15.0),
        ]
        report, acc, preds = suite._evaluate_scope_accuracy(samples, [5.0, 12.5, 15.0])
        assert report is not None
        assert [s.name for s in report.in_scope_samples] == ["mid", "high"]
        assert acc is not None
        # nd-native scale: predictions pass through the identity calibration.
        assert preds == [12.5, 15.0]

    def test_too_few_kept_charts_skips_the_reading(self):
        suite = self._make_suite([parse_declaration("nd<10.5")])
        samples = [
            _sample("low", "speed", 5.0),
            _sample("mid", "speed", 12.0),
        ]
        report, acc, preds = suite._evaluate_scope_accuracy(samples, [5.0, 12.5])
        assert report is not None
        assert acc is None and preds == []
