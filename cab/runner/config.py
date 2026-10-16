"""Configuration definitions for CAB-VA Benchmark Runner."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# Named domain-filter presets.
#
# A preset is a *named bundle* of dataset filter knobs.  Each entry declares the
# filter keys it wants to set; explicit CLI arguments always win over the preset
# (see BenchConfig.create).  Adding a new preset means adding one entry here --
# nothing else in the codebase hard-codes preset names any more.
#
# Supported keys per preset:
#   patterns          : include only these pattern labels
#   exclude_patterns  : drop these pattern labels
#   min_expected      : lower ng bound (inclusive)
#   max_expected      : upper ng bound (inclusive)
#   description       : human-readable one-liner
# ---------------------------------------------------------------------------
FILTER_PRESETS: dict[str, dict] = {
    "daniel": {
        "exclude_patterns": ["ln"],
        "min_expected": 10.5,
        "max_expected": 18.5,
        "description": "Daniel native target domain: non-LN, ng in [10.5, 18.5]",
    },
}

# Keys a preset may carry that map directly onto BenchConfig fields.
_PRESET_FILTER_KEYS: tuple[str, ...] = (
    "patterns",
    "exclude_patterns",
    "min_expected",
    "max_expected",
)


def get_filter_preset(name: str) -> dict:
    """Return the preset definition for *name* (case-insensitive).

    Raises ValueError with the list of known presets when *name* is unknown.
    """
    key = (name or "").lower().strip()
    if key not in FILTER_PRESETS:
        known = ", ".join(sorted(FILTER_PRESETS)) or "<none>"
        raise ValueError(f"Unknown filter preset {name!r}. Known presets: {known}")
    return FILTER_PRESETS[key]


def preset_excludes_pattern(name: str, pattern: str) -> bool:
    """True if preset *name* excludes *pattern* (case-insensitive substring)."""
    preset = get_filter_preset(name)
    target = pattern.lower()
    for pat in preset.get("exclude_patterns") or ():
        if target in str(pat).lower():
            return True
    return False


@dataclass(frozen=True)
class TierDefinition:
    """Specification for a difficulty evaluation tier."""

    tier_id: str
    name: str
    dir_name: str
    min_val: float
    max_val: float
    min_inclusive: bool = True
    max_inclusive: bool = True
    description: str = ""


TIER_ALL = "all"
TIER_NUMBER = "number"
TIER_GREEK = "greek"
TIER_INHUMAN = "inhuman"

TIER_DEFINITIONS: dict[str, TierDefinition] = {
    TIER_ALL: TierDefinition(
        tier_id="all",
        name="全域段 (All Range)",
        dir_name="all",
        min_val=-2.5,
        max_val=float("inf"),
        min_inclusive=True,
        max_inclusive=True,
        description="全难度区间全量测试 (>= -2.5)，反映算法综合实力与通用性",
    ),
    TIER_NUMBER: TierDefinition(
        tier_id="number",
        name="数字段 (Number Dan)",
        dir_name="number_dan",
        min_val=-2.5,
        max_val=10.5,
        min_inclusive=True,
        max_inclusive=False,  # [-2.5, 10.5)
        description="Intro 1~3 至 10th Dan ([-2.5, 10.5))，考察中低难度的渐进性与密集度感知",
    ),
    TIER_GREEK: TierDefinition(
        tier_id="greek",
        name="字母段 (Greek Dan)",
        dir_name="greek_dan",
        min_val=10.5,
        max_val=15.5,
        min_inclusive=True,
        max_inclusive=True,  # [10.5, 15.5]
        description="Alpha (11.0) 至 Epsilon (15.0) ([10.5, 15.5])，竞技与段位考试的核心高难区间",
    ),
    TIER_INHUMAN: TierDefinition(
        tier_id="inhuman",
        name="非人类段 (Inhuman Dan)",
        dir_name="inhuman_dan",
        min_val=15.5,
        max_val=float("inf"),
        min_inclusive=False,  # > 15.5
        max_inclusive=True,
        description="Zeta (16.0) 至 Theta/Kappa (18+) (> 15.5)，超高速超极限外推稳定性",
    ),
}

DEFAULT_TIERS = (TIER_ALL, TIER_NUMBER, TIER_GREEK, TIER_INHUMAN)


@dataclass
class BenchConfig:
    """Benchmark execution configuration."""

    mode: str = "standard"  # 'quick', 'standard', 'full'
    csv_path: str = "samples/4k/uuzs_bench.csv"
    samples_root: Optional[str] = None
    scale_type: str = "auto"  # 'ng', 'non-ng', 'auto'
    workers: int = field(default_factory=lambda: min(24, os.cpu_count() or 4))
    seed: int = 42

    # Tier mode: 'multi' (default, runs all 4 tiers), or specific tier ('all', 'number', 'greek', 'inhuman')
    tier_mode: str = "multi"

    # Rate Scaling Parameters
    rate_min: float = 1.01
    rate_max: float = 2.00
    rate_step: float = 0.05  # Standard: 0.05; Full: 0.01; Quick: 0.10

    # Adaptive two-phase rate sweep: coarse scan (step=0.1) + refinement on suspicious
    # maps/intervals (effective step = min(rate_step, rate_refine_step)).
    adaptive_rate: bool = True
    rate_refine_step: float = 0.02

    # Jitter Parameters
    jitter_sigma: float = 8.0
    jitter_max: float = 15.0

    # Short LN Parameters
    short_ln_min_dur: int = 20
    short_ln_max_dur: int = 100

    # Sampling Limits
    # None = process all matching samples in dataset
    sample_limit_accuracy: Optional[int] = None
    sample_limit_perturbation: Optional[int] = None

    # Sample Filtering & Domain Slicing (for debugging & targeted scope testing)
    patterns: Optional[Sequence[str]] = None
    exclude_patterns: Optional[Sequence[str]] = None
    min_expected: Optional[float] = None
    max_expected: Optional[float] = None
    name_filter: Optional[str] = None
    filter_preset: Optional[str] = None

    # Output directory
    output_dir: str = "results"

    # Speed-dimension (SP) provenance override.
    # When set to a results directory (e.g. "results_sunny_standard"), the
    # computation-speed dimension is NOT measured from the current run; instead
    # the SpeedResult is loaded from that directory's cab_benchmark_result.json.
    # Used by fast drop-in replacements (sunny-rs) so that the SP score keeps
    # reflecting the ORIGINAL algorithm's real-world speed rather than the
    # accelerated surrogate's. Applies to the "all" tier and is rescaled per
    # sub-tier from the same source when available.
    sp_source: Optional[str] = None

    def get_tier_list(self) -> list[TierDefinition]:
        """Return the list of TierDefinitions to evaluate."""
        mode = (self.tier_mode or "multi").lower().strip()
        if mode in ("multi", "all-tiers", "4-tiers"):
            return [TIER_DEFINITIONS[t] for t in DEFAULT_TIERS]
        if mode in ("number", "number_dan", "regular"):
            return [TIER_DEFINITIONS[TIER_NUMBER]]
        if mode in ("greek", "greek_dan"):
            return [TIER_DEFINITIONS[TIER_GREEK]]
        if mode in ("inhuman", "inhuman_dan"):
            return [TIER_DEFINITIONS[TIER_INHUMAN]]
        if mode in ("all", "all_range", "full"):
            return [TIER_DEFINITIONS[TIER_ALL]]
        return [TIER_DEFINITIONS[t] for t in DEFAULT_TIERS]

    def has_filters(self) -> bool:
        """Check if any dataset filters are active."""
        return any(
            [
                self.patterns is not None,
                self.exclude_patterns is not None,
                self.min_expected is not None,
                self.max_expected is not None,
                self.name_filter is not None,
                self.filter_preset is not None,
            ]
        )

    def get_filter_summary(self) -> str:
        """Return a human-readable summary of active dataset filters."""
        if not self.has_filters():
            return "None (Full Dataset)"

        parts: list[str] = []
        if self.filter_preset:
            parts.append(f"Preset: {self.filter_preset}")
        if self.patterns:
            parts.append(f"Include: {','.join(self.patterns)}")
        if self.exclude_patterns:
            parts.append(f"Exclude: {','.join(self.exclude_patterns)}")
        if self.min_expected is not None and self.max_expected is not None:
            parts.append(f"Difficulty: [{self.min_expected:.1f}, {self.max_expected:.1f}]")
        elif self.min_expected is not None:
            parts.append(f"Difficulty: >= {self.min_expected:.1f}")
        elif self.max_expected is not None:
            parts.append(f"Difficulty: <= {self.max_expected:.1f}")
        if self.name_filter:
            parts.append(f"Name ~ '{self.name_filter}'")

        return "; ".join(parts)

    @classmethod
    def create(cls, mode: str = "standard", **kwargs) -> BenchConfig:
        """Create configuration preset for quick, standard, or full benchmark."""
        cfg = cls(mode=mode)
        if mode == "quick":
            cfg.rate_step = 0.10
            cfg.sample_limit_accuracy = 120
            cfg.sample_limit_perturbation = 40
            cfg.adaptive_rate = False  # quick stays on the plain uniform grid
        elif mode == "standard":
            cfg.rate_step = 0.05  # refinement resolution for suspicious intervals
            cfg.sample_limit_accuracy = None  # Full 776 maps (100% complete dataset)
            cfg.sample_limit_perturbation = 300  # Stratified perturbation subset (was: full dataset)
            cfg.adaptive_rate = True  # coarse 0.1 sweep + 0.02 refinement on suspicious maps
        elif mode == "full":
            cfg.rate_step = 0.01  # Exact 100 micro-rate steps per Section 3.2
            cfg.sample_limit_accuracy = None
            cfg.sample_limit_perturbation = None  # full keeps every perturbation map
            cfg.adaptive_rate = True

        # Apply domain-filter preset if requested.  Explicit kwargs always win.
        preset_name = kwargs.get("filter_preset")
        if preset_name:
            preset = get_filter_preset(preset_name)  # validates the name
            cfg.filter_preset = preset_name.lower().strip()
            for key in _PRESET_FILTER_KEYS:
                if key in preset and kwargs.get(key) is None:
                    setattr(cfg, key, preset[key])

        for k, v in kwargs.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)

        return cfg
