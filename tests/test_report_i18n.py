"""Guards for the bilingual report plumbing.

The reports are rendered from inline ``(zh, en)`` pairs; these tests keep the
English side from silently falling back to Chinese when someone adds a pattern
or a verdict.
"""

import re

from cab.report.profiling import (
    VERDICT_ADEQUATE,
    VERDICT_INSUFFICIENT,
    VERDICT_STRENGTH,
    VERDICT_WEAKNESS,
    PatternVerdict,
    describe_pattern,
    describe_pattern_en,
)

CJK = re.compile(r"[\u4e00-\u9fff]")
PATTERNS = ["course", "jack", "ln", "speed", "stamina", "tech", "unsigned"]
VERDICTS = [VERDICT_STRENGTH, VERDICT_ADEQUATE, VERDICT_WEAKNESS, VERDICT_INSUFFICIENT]


def test_english_pattern_descriptions_have_no_cjk():
    for pattern in PATTERNS:
        text = describe_pattern_en(pattern)
        assert text, pattern
        assert not CJK.search(text), f"{pattern!r} -> {text!r} still contains chinese"


def test_chinese_pattern_descriptions_still_localised():
    # Guards the other direction: the zh report must not accidentally switch to
    # the English table.
    for pattern in PATTERNS:
        assert CJK.search(describe_pattern(pattern)), pattern


def test_unknown_pattern_falls_back_to_its_own_id():
    assert describe_pattern_en("mystery") == "mystery"


def test_every_verdict_has_an_english_label():
    for verdict in VERDICTS:
        v = PatternVerdict(pattern="jack", rmse=1.0, samples=30, ratio=1.0, verdict=verdict)
        assert v.label_en, verdict
        assert not CJK.search(v.label_en), f"{verdict!r} -> {v.label_en!r}"


def test_pick_respects_lang():
    from cab.report.markdown import _pick

    assert _pick("中文", "English", "zh") == "中文"
    assert _pick("中文", "English", "en") == "English"
    assert _pick("中文", "English", "zh-CN") == "中文"   # anything else falls back to zh


def test_comparisons_are_padded():
    """`<3` is a heart emoji to some renderers, so comparisons get spaces."""
    from cab.report.markdown import _space_comparisons

    assert _space_comparisons("(检测到窗口密度盲视 (<3%))") == "(检测到窗口密度盲视 ( < 3%))"
    assert _space_comparisons("ND>=10.5") == "ND >= 10.5"
    assert _space_comparisons("ratio<0.8") == "ratio < 0.8"


def test_comparisons_already_spaced_are_left_alone():
    from cab.report.markdown import _space_comparisons

    for text in ("`> 1.20`", "$> 15.5$", "a < b", "`<= 0.80`", "| :---: |"):
        assert _space_comparisons(text) == text, text


def test_images_are_linked_relative_to_the_report():
    """Absolute paths break as soon as the folder is moved or shared."""
    from pathlib import Path

    from cab.report.markdown import _rel_img

    base = Path("/tmp/run")
    assert _rel_img("/tmp/run/cab_radar_chart.png", base) == "cab_radar_chart.png"
    assert _rel_img("/tmp/run/all/cab_radar_chart.png", base) == "all/cab_radar_chart.png"
