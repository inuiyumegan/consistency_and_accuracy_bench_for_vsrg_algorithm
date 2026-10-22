"""Declared difficulty-scope ("abstention") declarations for specialised algorithms.

Motivation
----------
Some algorithms are deliberately built for a slice of the difficulty space:
Daniel is a pure-Rice algorithm with no LN model, and several dan estimators
only cover the higher tiers. Such an algorithm cannot be scored fairly an
all-round benchmark, yet scoring it only on its own slice would make its score
incomparable with general-purpose algorithms.

The benchmark resolves this **without any penalty**:

* The algorithm (or its evaluator) declares, up front, which beatmaps it does
  not attempt. The declaration is explicit CLI input -- the benchmark never
  infers or adapts to an algorithm's behaviour on its own.
* The **headline CAB score and every one of the six dimensions are still
  computed over the full dataset**. A declaration never changes them, so
  algorithms remain directly comparable and over-declaring buys nothing.
* On top of that, a **second Accuracy reading** is computed over the *kept*
  (in-scope) beatmaps only, **with its own freshly fitted anchors**. This lets a
  specialised algorithm show how well it does inside the slice it actually
  targets, while the headline number keeps telling the whole story.

Both readings are reported side by side; the reader is left to judge. Nothing
is hidden behind a weighting constant.

Usage telemetry
---------------
``USAGE_SHARES`` records how often each beatmap family is analysed in the wild,
from the published ManiaMapAnalyser dashboard
(https://mma-stats.leoblack.top/, 90-day window, snapshot 2026-10-16;
~6.68M analyses total, 6.37M of which carry a numeric reading). It is used
**only for reporting** how much real-world usage a declaration gives up -- it
never feeds a score.

Note on the LN family
---------------------
The dashboard classifies beatmaps into ``rc`` / ``hb`` / ``mix`` / ``sv`` /
``ln`` by LN-note ratio. Ordinary players mean something much broader by "an LN
chart" -- it includes hybrid and mixed charts. Following that, ``hb`` + ``mix``
+ ``ln`` are folded into a single **LN family** bucket. ``sv`` (scroll-velocity)
charts are excluded entirely: their difficulty cannot be expressed on the
numeric-difficulty scale, so they are neither RC nor LN.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from cab.parser.bench_loader import BenchmarkSample

# ---------------------------------------------------------------------------
# Usage telemetry (reporting only -- never feeds a score)
# ---------------------------------------------------------------------------

#: Beatmap families that make up the "LN family" bucket. Ordinary players lump
#: these together as "LN charts"; the dashboard keeps them separate.
LN_FAMILY_MODES = ("hb", "mix", "ln")

#: Share of real-world analyses per beatmap family, from the ManiaMapAnalyser
#: usage dashboard (90-day window, ~6.68M analyses, snapshot 2026-10-16).
#: ``sv`` is deliberately absent: its difficulty has no numeric-difficulty
#: representation, so it is outside this benchmark's scope.
USAGE_SHARES: Dict[str, float] = {
    "rc": 0.661,
    "ln": 0.160 + 0.108 + 0.024,  # hb + mix + ln, folded into one family
}

#: Normalised so the two buckets sum to 1.0 (``sv`` removed from the denominator).
_USAGE_TOTAL = sum(USAGE_SHARES.values())
NORMALISED_USAGE: Dict[str, float] = {
    k: v / _USAGE_TOTAL for k, v in USAGE_SHARES.items()
}

#: Benchmark pattern -> usage bucket. Patterns not listed fall back to ``rc``.
PATTERN_TO_BUCKET: Dict[str, str] = {
    "ln": "ln",
    "course": "ln",  # course/dan marathons are overwhelmingly LN-heavy
    "jack": "rc",
    "speed": "rc",
    "stamina": "rc",
    "tech": "rc",
    "unsigned": "rc",
}

DEFAULT_BUCKET = "rc"

#: Real-world usage share per numeric-difficulty percentile, derived from the
#: dashboard's numeric histogram (6.37M analyses carry a numeric reading;
#: 0.25-wide buckets). Used only to report how much usage a difficulty-range
#: declaration gives up.
#:
#: Tuple layout: ``(lower_edge, cumulative_share_below)`` -- i.e. the fraction
#: of all analyses whose numeric difficulty is strictly below ``lower_edge``.
CUMULATIVE_USAGE: List[tuple] = [
    (9.0, 0.4277),
    (9.5, 0.4487),
    (10.0, 0.5226),
    (10.5, 0.5693),
    (11.0, 0.6033),
    (11.5, 0.6332),
    (12.0, 0.6707),
    (13.0, 0.7566),
    (14.0, 0.8575),
    (15.0, 0.9277),
    (15.5, 0.9528),
    (16.0, 0.9660),
    (17.0, 0.9857),
]


# ---------------------------------------------------------------------------
# Declaration model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AbstentionDeclaration:
    """A single declared difficulty scope, expressed as one CLI argument.

    The declaration names what the algorithm **does not attempt**:

    ``patterns`` names the pattern classes it skips.
    ``min_nd`` / ``max_nd`` bound the difficulty range it does attempt; charts
    outside ``[min_nd, max_nd]`` are out of scope. Either or both may be set.

    Multiple declarations are combined with **OR** semantics: a chart is out of
    scope when *any* declaration excludes it.
    """

    patterns: tuple = ()
    min_nd: Optional[float] = None
    max_nd: Optional[float] = None

    def describe(self) -> str:
        bits: List[str] = []
        if self.patterns:
            bits.append("skip=" + ",".join(self.patterns))
        if self.min_nd is not None:
            bits.append(f"skip ND<{self.min_nd:g}")
        if self.max_nd is not None:
            bits.append(f"skip ND>{self.max_nd:g}")
        return " ".join(bits) or "(empty declaration)"


#: Pattern names accepted in a ``pattern:`` term.
KNOWN_PATTERNS = frozenset(
    {"ln", "course", "jack", "speed", "stamina", "tech", "unsigned"}
)


def _split_terms(text: str) -> List[str]:
    """Split a declaration into terms without breaking ``pattern:a,b`` lists.

    Terms are separated by whitespace; a comma only separates pattern names,
    so ``pattern:ln,tech`` stays one term while ``pattern:ln nd<10.5`` is two.
    """
    terms: List[str] = []
    for chunk in text.split():
        # A comma that is NOT part of a "key:value" pair is a bare separator.
        if ":" not in chunk and "," in chunk:
            terms.extend(part for part in chunk.split(",") if part)
        else:
            terms.append(chunk)
    return terms


def parse_declaration(raw: str) -> AbstentionDeclaration:
    """Parse one ``--declare-scope`` argument.

    The grammar names what is **given up**; the bound is stored as the floor /
    ceiling of the *attempted* window, and the window is always **closed** at
    the bound (the boundary value itself is kept -- ``<`` / ``<=`` and ``>``
    / ``>=`` behave identically). Accepted terms (whitespace separated, any
    order)::

        pattern:ln,tech        # skip these pattern classes
        nd<10.5  (== nd<=10.5) # skip what is below 10.5  -> keep ND >= 10.5
        nd>15.5  (== nd>=15.5) # skip what is above 15.5  -> keep ND <= 15.5

    ``difficulty`` is accepted as an alias of ``nd``; both refer to the
    Numeric Difficulty scale.
    """
    patterns: List[str] = []
    lower_bound: Optional[float] = None  # inclusive floor of the kept window
    upper_bound: Optional[float] = None  # inclusive ceiling of the kept window

    text = raw.strip()
    if not text:
        raise ValueError("empty scope declaration")

    for token in _split_terms(text):
        low = token.lower()
        if ":" in low:
            key, _, value = low.partition(":")
            if key not in ("pattern", "patterns"):
                raise ValueError(f"unknown scope key: {key!r} in {token!r}")
            for name in value.replace("|", ",").split(","):
                name = name.strip()
                if not name:
                    continue
                if name not in KNOWN_PATTERNS:
                    raise ValueError(
                        f"unknown pattern {name!r} in {token!r}; "
                        f"expected one of {', '.join(sorted(KNOWN_PATTERNS))}"
                    )
                patterns.append(name)
            continue

        # Difficulty terms: the comparison names what is DROPPED, so the
        # bound kept in the window is the mirror image of the operator.
        for op in ("<=", ">=", "<", ">"):
            if op in low:
                key, _, value = low.partition(op)
                if key not in ("nd", "difficulty"):
                    raise ValueError(f"unknown scope key: {key!r} in {token!r}")
                try:
                    bound = float(value)
                except ValueError:
                    raise ValueError(f"non-numeric difficulty bound in {token!r}") from None
                if op in ("<", "<="):
                    # "skip below X" -> the kept window starts at X
                    lower_bound = bound if lower_bound is None else max(lower_bound, bound)
                else:
                    # "skip above X" -> the kept window ends at X
                    upper_bound = bound if upper_bound is None else min(upper_bound, bound)
                break
        else:
            raise ValueError(f"unrecognised scope term: {token!r}")

    if lower_bound is not None and upper_bound is not None and lower_bound > upper_bound:
        # e.g. "nd<10.5,nd>9" -> keep 10.5..9, an empty window that silently
        # nullified the whole in-scope reading.
        raise ValueError(
            f"contradictory scope: lower bound {lower_bound} > upper bound {upper_bound}"
        )
    if not patterns and lower_bound is None and upper_bound is None:
        raise ValueError("empty scope declaration: specify at least one pattern or bound")

    return AbstentionDeclaration(
        patterns=tuple(dict.fromkeys(patterns)),  # de-dupe, keep order
        min_nd=lower_bound,
        max_nd=upper_bound,
    )


# ---------------------------------------------------------------------------
# Sample partitioning
# ---------------------------------------------------------------------------


def sample_in_scope(
    sample: BenchmarkSample,
    declarations: Sequence[AbstentionDeclaration],
) -> bool:
    """Whether ``sample`` falls inside the declared scope.

    A sample is out of scope when **any** declaration excludes it (OR
    semantics). A declaration excludes a sample when its pattern is listed, or
    when its ``expected`` difficulty falls outside ``[min_nd, max_nd]``.
    """
    if not declarations:
        return True
    pat = sample.pattern.lower()
    for d in declarations:
        if d.patterns and pat in d.patterns:
            return False
        if d.min_nd is not None and sample.expected < d.min_nd:
            return False
        if d.max_nd is not None and sample.expected > d.max_nd:
            return False
    return True


def _split(
    samples: Sequence[BenchmarkSample],
    declarations: Sequence[AbstentionDeclaration],
) -> tuple[List[BenchmarkSample], List[BenchmarkSample]]:
    kept: List[BenchmarkSample] = []
    dropped: List[BenchmarkSample] = []
    for s in samples:
        (kept if sample_in_scope(s, declarations) else dropped).append(s)
    return kept, dropped


# ---------------------------------------------------------------------------
# Usage-coverage reporting
# ---------------------------------------------------------------------------


def _pattern_share(pattern: str) -> float:
    bucket = PATTERN_TO_BUCKET.get(pattern.lower(), DEFAULT_BUCKET)
    return NORMALISED_USAGE.get(bucket, 0.0)


def _band_usage_below(threshold: float) -> float:
    """Real-world usage share with ND strictly below ``threshold``."""
    lo, hi = CUMULATIVE_USAGE[0], CUMULATIVE_USAGE[-1]
    if threshold <= lo[0]:
        return 0.0
    if threshold >= hi[0]:
        return hi[1]
    for (x0, c0), (x1, c1) in zip(CUMULATIVE_USAGE, CUMULATIVE_USAGE[1:]):
        if x0 <= threshold <= x1:
            if x1 == x0:
                return c0
            frac = (threshold - x0) / (x1 - x0)
            return c0 + frac * (c1 - c0)
    return hi[1]


def _band_usage_above(threshold: float) -> float:
    """Real-world usage share with ND strictly above ``threshold``."""
    return max(0.0, 1.0 - _band_usage_below(threshold))


def estimate_out_of_scope_usage(
    declarations: Sequence[AbstentionDeclaration],
) -> float:
    """Fraction of real-world usage the declared scope gives up, in ``[0, 1]``.

    Model: pattern family and difficulty are treated as independent, so the
    share of usage that is *in scope* is::

        P(in scope) = sum_over_kept_buckets( W[bucket] ) * P(ND within bounds)

    The complement is what the declaration gives up. This is a **reporting
    figure only** -- no score depends on it.
    """
    if not declarations:
        return 0.0

    # OR semantics: a sample survives only when *every* declaration keeps it.
    # Declared patterns are mapped through the same bucket table the usage
    # weights use: ``pattern:course`` must drop the ``ln`` bucket too (a
    # ``ln course`` chart IS ln content), and ``pattern:tech`` must drop the
    # ``tech`` bucket.  The previous literal ``"ln" in patterns`` test made
    # every non-ln declaration a no-op.
    drop_buckets: set = set()
    min_nds: List[float] = []
    max_nds: List[float] = []
    for d in declarations:
        for pat in d.patterns:
            key = pat.strip().lower()
            if not key:
                continue
            drop_buckets.add(PATTERN_TO_BUCKET.get(key, key))
        if d.min_nd is not None:
            min_nds.append(d.min_nd)
        if d.max_nd is not None:
            max_nds.append(d.max_nd)

    floor = max(min_nds) if min_nds else None
    ceiling = min(max_nds) if max_nds else None

    # Probability the difficulty lands inside [floor, ceiling] (catch-all: 1.0)
    band = 1.0
    if floor is not None:
        band -= _band_usage_below(floor)
    if ceiling is not None:
        band -= _band_usage_above(ceiling)
    band = max(0.0, band)

    kept_share = 0.0
    for bucket, w in NORMALISED_USAGE.items():
        if bucket in drop_buckets:
            continue
        kept_share += w * band

    return max(0.0, min(1.0, 1.0 - kept_share))


def describe_scope(declarations: Sequence[AbstentionDeclaration]) -> str:
    """Human-readable description of the *attempted* scope.

    Describes what remains rather than what is dropped, e.g. a ``pattern:ln
    nd<10.5`` declaration reads as ``4K RC, ND >= 10.5``.
    """
    if not declarations:
        return "Full dataset"

    excluded_patterns: List[str] = []
    min_nds: List[float] = []
    max_nds: List[float] = []
    for d in declarations:
        for p in d.patterns:
            if p not in excluded_patterns:
                excluded_patterns.append(p)
        if d.min_nd is not None:
            min_nds.append(d.min_nd)
        if d.max_nd is not None:
            max_nds.append(d.max_nd)

    parts: List[str] = []
    if excluded_patterns:
        # Anything not excluded is still attempted.
        labels = [p.upper() for p in excluded_patterns]
        parts.append("excluding " + ", ".join(labels))
    else:
        parts.append("all patterns")

    if min_nds:
        parts.append(f"ND >= {max(min_nds):g}")
    if max_nds:
        parts.append(f"ND <= {min(max_nds):g}")

    return "4K, " + ", ".join(parts)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


@dataclass
class ScopeReport:
    """Outcome of applying scope declarations to a sample set."""

    declarations: List[AbstentionDeclaration] = field(default_factory=list)
    out_of_scope_samples: List[BenchmarkSample] = field(default_factory=list)
    in_scope_samples: List[BenchmarkSample] = field(default_factory=list)
    out_of_scope_usage: float = 0.0
    description: str = "Full dataset"

    @property
    def is_active(self) -> bool:
        return bool(self.declarations) and bool(self.out_of_scope_samples)

    def describe(self) -> str:
        if not self.declarations:
            return "Full dataset (no declared scope)."
        return (
            f"Declared scope: {self.description} | "
            f"out of scope {len(self.out_of_scope_samples)}/"
            f"{len(self.out_of_scope_samples) + len(self.in_scope_samples)} charts "
            f"({self.out_of_scope_usage * 100:.1f}% of real-world usage)"
        )


# Backwards-compatible alias: the concept was previously called "abstention".
AbstentionReport = ScopeReport


def apply_declarations(
    samples: Sequence[BenchmarkSample],
    declarations: Sequence[AbstentionDeclaration],
) -> ScopeReport:
    """Split ``samples`` into in-scope / out-of-scope and report usage coverage.

    No penalty is attached: the caller uses ``in_scope_samples`` to compute the
    secondary (in-scope) Accuracy reading and leaves every headline metric over
    the full dataset.
    """
    declarations = list(declarations)
    if not declarations:
        return ScopeReport(declarations=[], in_scope_samples=list(samples))

    kept, dropped = _split(samples, declarations)
    return ScopeReport(
        declarations=declarations,
        out_of_scope_samples=dropped,
        in_scope_samples=kept,
        out_of_scope_usage=estimate_out_of_scope_usage(declarations),
        description=describe_scope(declarations),
    )
