"""Unit tests for programmatic domain profiling (strengths / weaknesses)."""

from cab.report.profiling import (
    VERDICT_ADEQUATE,
    VERDICT_INSUFFICIENT,
    VERDICT_STRENGTH,
    VERDICT_WEAKNESS,
    profile_domains,
)


def test_profile_basic_strength_and_weakness():
    # Baseline = median of [0.3, 1.0, 2.0] = 1.0
    rmse = {"jack": 0.3, "speed": 1.0, "tech": 2.0}
    counts = {"jack": 50, "speed": 50, "tech": 50}
    prof = profile_domains(rmse, pattern_counts=counts)

    assert prof.baseline_rmse == 1.0
    assert [v.pattern for v in prof.strengths] == ["jack"]
    assert [v.pattern for v in prof.adequate] == ["speed"]
    assert [v.pattern for v in prof.weaknesses] == ["tech"]

    assert prof.has_judged is True
    by_pat = {v.pattern: v for v in prof.verdicts}
    assert by_pat["jack"].ratio == 0.3
    assert by_pat["jack"].label == "擅长"
    assert by_pat["tech"].label == "不擅长"


def test_profile_ratio_boundaries_are_inclusive():
    # baseline = median([0.8, 1.0, 1.2]) = 1.0
    rmse = {"a": 0.8, "b": 1.0, "c": 1.2}
    counts = {"a": 30, "b": 30, "c": 30}
    prof = profile_domains(rmse, pattern_counts=counts)
    by_pat = {v.pattern: v for v in prof.verdicts}
    # Exactly 0.8 -> strength, exactly 1.2 -> adequate (ratio <= threshold wins)
    assert by_pat["a"].verdict == VERDICT_STRENGTH
    assert by_pat["b"].verdict == VERDICT_ADEQUATE
    assert by_pat["c"].verdict == VERDICT_ADEQUATE


def test_profile_insufficient_samples_never_judged():
    rmse = {"jack": 0.1, "ln": 5.0}
    counts = {"jack": 5, "ln": 60}  # jack below min_samples
    prof = profile_domains(rmse, pattern_counts=counts, min_samples=20)

    by_pat = {v.pattern: v for v in prof.verdicts}
    assert by_pat["jack"].verdict == VERDICT_INSUFFICIENT
    assert by_pat["jack"].is_judged is False
    assert [v.pattern for v in prof.insufficient] == ["jack"]
    # ln alone is judged against the baseline (median of the two values).
    assert by_pat["ln"].is_judged is True


def test_profile_empty_input_is_safe():
    prof = profile_domains({})
    assert prof.baseline_rmse == 0.0
    assert prof.verdicts == []
    assert prof.has_judged is False


def test_profile_without_counts_judges_everything():
    rmse = {"jack": 0.3, "tech": 2.0}
    prof = profile_domains(rmse)  # no counts supplied
    assert all(v.samples == -1 for v in prof.verdicts)
    assert len(prof.strengths) == 1
    assert len(prof.weaknesses) == 1


def test_profile_canonical_ordering_is_stable():
    rmse = {"unsigned": 1.0, "tech": 1.0, "jack": 1.0, "course": 1.0}
    prof = profile_domains(rmse)
    order = [v.pattern for v in prof.verdicts]
    assert order == ["jack", "tech", "course", "unsigned"]


def test_profile_ignores_non_positive_rmse():
    rmse = {"jack": 0.0, "speed": 1.0, "tech": 1.0}
    prof = profile_domains(rmse)
    patterns = [v.pattern for v in prof.verdicts]
    assert "jack" not in patterns
