"""Programmatic algorithm domain profiling (strengths / weaknesses).

The standard benchmark keeps the accuracy dimension on the *full* dataset, which
mixes every key-pattern together.  A single global RMSE therefore cannot say
which kinds of beatmaps an algorithm handles well and which it struggles with.
This module derives that statement from the data itself, so the report stays
maintainable and free of hand-written prose.

Method (cohort-internal baseline, self-contained, unit-testable):

1.  Collect the per-pattern RMSE that ``AccuracyResult.stratified_rmse`` already
    carries, plus the per-pattern sample counts from the dataset stats.
2.  Take the *median* of those per-pattern RMSEs as the algorithm's own baseline
    (a robust centre for the dispersion of its error across patterns).
3.  A pattern is called out when its error sits materially above or below that
    baseline:

        ratio = rmse[pattern] / median(rmse over all patterns)
        ratio <= strength_ratio (default 0.8)   -> STRENGTH   ("擅长")
        ratio <= weakness_ratio (default 1.2)   -> ADEQUATE   ("达标")
        otherwise                               -> WEAKNESS   ("不擅长")

4.  Patterns with fewer than ``min_samples`` beatmaps are never judged -- they
    are flagged as under-sampled so a two-map fluke cannot masquerade as a
    strength or a weakness.

Everything here is pure: given the same inputs it returns the same verdicts,
with no hidden state and no dependency on other runs' result directories.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Optional, Sequence


# --- Verdict labels -------------------------------------------------------

VERDICT_STRENGTH = "strength"
VERDICT_ADEQUATE = "adequate"
VERDICT_WEAKNESS = "weakness"
VERDICT_INSUFFICIENT = "insufficient"

_DEFAULT_MIN_SAMPLES = 20
_DEFAULT_STRENGTH_RATIO = 0.8
_DEFAULT_WEAKNESS_RATIO = 1.2

# Human-readable Chinese labels used by the markdown report.
VERDICT_LABELS: Dict[str, str] = {
    VERDICT_STRENGTH: "擅长",
    VERDICT_ADEQUATE: "达标",
    VERDICT_WEAKNESS: "不擅长",
    VERDICT_INSUFFICIENT: "样本不足",
}

# Same verdicts for the English report.
VERDICT_LABELS_EN: Dict[str, str] = {
    VERDICT_STRENGTH: "Strength",
    VERDICT_ADEQUATE: "Adequate",
    VERDICT_WEAKNESS: "Weakness",
    VERDICT_INSUFFICIENT: "Under-sampled",
}

_PATTERN_DESCRIPTIONS: Dict[str, str] = {
    "course": "长曲马拉松 / 段位组曲",
    "jack": "连打 (Jack)",
    "ln": "长条 (LN)",
    "speed": "高速 (Speed)",
    "stamina": "耐力 (Stamina)",
    "tech": "技术 (Tech)",
    "unsigned": "未分类 (Unsigned)",
}

# The canonical ordering used whenever the report renders a pattern list, so the
# output is stable across runs regardless of dict insertion order.
_PATTERN_ORDER: tuple[str, ...] = (
    "jack",
    "speed",
    "stamina",
    "tech",
    "ln",
    "course",
    "unsigned",
)


_PATTERN_DESCRIPTIONS_EN: Dict[str, str] = {
    "course": "marathon / dan course",
    "jack": "jacks / chordjacks",
    "ln": "long notes",
    "speed": "high-speed streams",
    "stamina": "stamina drain",
    "tech": "technical",
    "unsigned": "unclassified",
}


def describe_pattern_en(pattern: str) -> str:
    """English counterpart of :func:`describe_pattern`."""
    key = pattern.strip().lower()
    for sub in (key, key.split(" ")[0]):
        if sub in _PATTERN_DESCRIPTIONS_EN:
            return _PATTERN_DESCRIPTIONS_EN[sub]
    return pattern


def describe_pattern(pattern: str) -> str:
    """Return a human-readable description for a pattern label."""
    key = (pattern or "").lower()
    if key in _PATTERN_DESCRIPTIONS:
        return _PATTERN_DESCRIPTIONS[key]
    return "常规单曲键型"


@dataclass
class PatternVerdict:
    """The profiling verdict for a single key-pattern."""

    pattern: str
    rmse: float
    samples: int
    ratio: float
    verdict: str

    @property
    def label(self) -> str:
        return VERDICT_LABELS.get(self.verdict, self.verdict)

    @property
    def label_en(self) -> str:
        return VERDICT_LABELS_EN.get(self.verdict, self.verdict)

    @property
    def described_pattern(self) -> str:
        return describe_pattern(self.pattern)

    @property
    def is_judged(self) -> bool:
        return self.verdict != VERDICT_INSUFFICIENT


@dataclass
class DomainProfile:
    """Aggregate strengths / weaknesses profile of an algorithm."""

    baseline_rmse: float
    verdicts: List[PatternVerdict] = field(default_factory=list)
    min_samples: int = _DEFAULT_MIN_SAMPLES

    def by_verdict(self, verdict: str) -> List[PatternVerdict]:
        return [v for v in self.verdicts if v.verdict == verdict]

    @property
    def strengths(self) -> List[PatternVerdict]:
        return self.by_verdict(VERDICT_STRENGTH)

    @property
    def weaknesses(self) -> List[PatternVerdict]:
        return self.by_verdict(VERDICT_WEAKNESS)

    @property
    def adequate(self) -> List[PatternVerdict]:
        return self.by_verdict(VERDICT_ADEQUATE)

    @property
    def insufficient(self) -> List[PatternVerdict]:
        return self.by_verdict(VERDICT_INSUFFICIENT)

    @property
    def has_judged(self) -> bool:
        return any(v.is_judged for v in self.verdicts)


def _ordered_patterns(patterns: Mapping[str, float]) -> List[str]:
    """Sort patterns by the canonical order, appending any unknown ones."""
    known = [p for p in _PATTERN_ORDER if p in patterns]
    extra = sorted(p for p in patterns if p not in _PATTERN_ORDER)
    return known + extra


def profile_domains(
    stratified_rmse: Mapping[str, float],
    pattern_counts: Optional[Mapping[str, int]] = None,
    min_samples: int = _DEFAULT_MIN_SAMPLES,
    strength_ratio: float = _DEFAULT_STRENGTH_RATIO,
    weakness_ratio: float = _DEFAULT_WEAKNESS_RATIO,
) -> DomainProfile:
    """Derive a strengths/weaknesses profile from per-pattern RMSE values.

    Args:
        stratified_rmse: ``{pattern: rmse}`` as produced by the accuracy metric.
        pattern_counts:  Optional ``{pattern: sample_count}``.  When omitted,
            every pattern is treated as having enough samples (no
            "insufficient" verdicts are emitted).
        min_samples:     Patterns below this count are flagged as under-sampled
            and never judged.
        strength_ratio:  ratio at or below which a pattern counts as a strength.
        weakness_ratio:  ratio above which a pattern counts as a weakness.

    Returns:
        A :class:`DomainProfile`.  ``baseline_rmse`` is the median of the
        per-pattern RMSEs; when there are no (finite, positive) values the
        profile is empty and carries a zero baseline.
    """
    usable = {
        str(pat).lower(): float(rmse)
        for pat, rmse in (stratified_rmse or {}).items()
        if rmse is not None and float(rmse) > 0.0
    }
    if not usable:
        return DomainProfile(baseline_rmse=0.0, verdicts=[], min_samples=min_samples)

    values = sorted(usable.values())
    mid = len(values) // 2
    if len(values) % 2 == 1:
        baseline = values[mid]
    else:
        baseline = (values[mid - 1] + values[mid]) / 2.0

    counts = {str(k).lower(): int(v) for k, v in (pattern_counts or {}).items()}

    verdicts: List[PatternVerdict] = []
    for pat in _ordered_patterns(usable):
        rmse = usable[pat]
        ratio = rmse / baseline if baseline > 0 else 1.0
        samples = counts.get(pat)

        if samples is not None and samples < min_samples:
            verdict = VERDICT_INSUFFICIENT
        elif ratio <= strength_ratio:
            verdict = VERDICT_STRENGTH
        elif ratio <= weakness_ratio:
            verdict = VERDICT_ADEQUATE
        else:
            verdict = VERDICT_WEAKNESS

        verdicts.append(
            PatternVerdict(
                pattern=pat,
                rmse=rmse,
                samples=samples if samples is not None else -1,
                ratio=ratio,
                verdict=verdict,
            )
        )

    return DomainProfile(baseline_rmse=baseline, verdicts=verdicts, min_samples=min_samples)


def format_pattern_list(items: Sequence[PatternVerdict]) -> str:
    """Render a compact ``pattern (ratio)`` list for markdown prose."""
    if not items:
        return "—"
    return "、".join(f"`{v.pattern}` ({v.ratio:.2f}x)" for v in items)
