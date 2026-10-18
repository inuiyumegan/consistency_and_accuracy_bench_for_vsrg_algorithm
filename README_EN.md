# CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm

**A standardized consistency & accuracy benchmark for vertical-scrolling rhythm game difficulty algorithms**

**中文版**: [README.md](./README.md)

CAB-VA is a standardized black-box benchmarking framework for difficulty rating algorithms in vertical-scrolling rhythm games (VSRG, currently focused on osu!mania 4K). Comparable to ARC-AGI-2 in the AI world, it throws difficulty algorithms into a "harsh crucible" of rate scaling, micro-noise and temporal edge cases, systematically exposing four classes of counter-intuitive flaws:

- **Rate Inversion**: at higher rates the physical demands grow, yet the algorithm's output drops instead;
- **Cliffing**: hard thresholds make difficulty leap or crash under tiny perturbations;
- **Jitter Sensitivity**: a few milliseconds of timing offset cause wild rating swings;
- **Short LN Inflation**: 20ms–100ms short LNs are treated as full-length holds, causing inflation.

## Why CAB-VA (A Word from the Author)

Mania difficulty algorithms are multiplying fast these days — no longer limited to developer-led projects like MSD or [Interlude](https://github.com/YAVSRG/Interlude), or long-labored reworks like [SunnyXXY Rework](https://github.com/sunnyxxy/Star-Rating-Rebirth) that take years to forge. Many more targeted, faster-iterating algorithms such as [Companella](https://github.com/LeoBlackMT/osumania_map_analyser), [Daniel](https://github.com/TheBagelOfMan/Daniel) and [Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) are now springing up.

Training approaches are expanding too — no longer just building "secondary algorithms" with linear models or decision trees fit to a training set. Arily ([Arily](https://github.com/ppy-sb/rosu-pp)), the owner of the ppy.sb server, pioneered a surprisingly solid pp system built from a mountain of replays, and more players will likely follow that path. A flourishing scene, everything blooming at once…

The community is increasingly hungry for a more authoritative way to show just how badass their algorithm is.

So here it is — Consistency and Accuracy Bench for VSRG Algorithm, CAB-VA for short — fumbled into the world by yours truly, a retired, invisible small-timer. Call it whatever you like.

> **Note**: Please don't act like certain LLMs and just hammer this benchmark with decision trees to farm a high score. That's textbook overfitting, and you'll never get your algorithm back into shape afterwards.
The composite score (CAB-Score) reflects an algorithm's overall engineering robustness; for everyday players, just looking at which algorithm has a higher **ISACC** is all you need.
Inclusion is purely up to mood; PRs are welcome if this blows up.

*The rest of this document was mostly written by AI. Bear with it.*

## Quick Start

Requirements: Python 3.10+ (numpy / scipy / pandas / matplotlib / rich).

```bash
# Install
pip install -e .

# Show all options
./cab_run.sh run --help

# Quick check (sampled mode, for development)
./cab_run.sh run --algorithm daniel --mode quick --output-dir results_quick

# Standard full benchmark (all 4 tiers in a single run by default)
./cab_run.sh run --algorithm daniel --scale daniel --mode standard --output-dir results

# Plug in any external algorithm (plain-text protocol, zero code intrusion)
./cab_run.sh run --cmd "./my_algorithm --file {input}" --mode quick
```

## Evaluation Dimensions (ACC backbone + five multiplicative discounts)

| Dimension | Discount β | Key metrics |
| :--- | :---: | :--- |
| Ground-Truth Accuracy (ACC) | **backbone** | Sub-pattern-weighted ND-RMSE (family weight × within-family 3:1), MAE, Spearman ρ, within-tier dispersion (WTD); includes the structural-inversion penalty |
| Anti-Rate-Inversion (AIN) | 15% | Monotonicity under 1.01x–2.0x rate scaling |
| Anti-Short-LN Inflation (ASLN) | 15% | 20ms–100ms short-LN injection; **two-sided tolerance band** (target ITR ≈ 7%) + **continuous Hold-Off gate** (LN-blind / brute-force tap stripping gated to 0) |
| Local Continuity (CT) | 10% | High-density micro-stepping + local Lipschitz cliff detection |
| Computation Speed (SP) | 5% | Compute cost in ms/s |
| Anti-Jitter (AJ) | 5% | **OD-adaptive microscopic jitter** (9–11ms within hit window) + **intra-window arrangement sensitivity probe gate** (penalising brute-force density algorithms) |

> **Note**: as of 2026-10-17 the total is no longer a weighted average but **ACC backbone × per-dimension discounts**:
> $$\text{Total} = \text{ACC}\times\prod_i\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$
> "Discount β" means **how much of the total is lost if that dimension scores 0** — not a weight: **a perfect score adds nothing** (so saturated dimensions such as CT, where 7 of 10 algorithms hit exactly 100, no longer inflate every total), and **a weak dimension cannot be bought back by a strong one**. The ACC dimension also carries the **structural-inversion ("倒挂") penalty** (multi-scale dip severity, capped at −30 points; see "ACC Metric: Sub-Pattern Weighting"). Sub-tiers evaluate only ACC/ASLN/SP/AJ; the dynamic dimensions simply drop out of the product (no renormalisation required).

## Cross-Algorithm Scoreboard

The table below is the **standard-mode, all-range** cross-benchmark: **ten current algorithms plus one historical reference variant** (`osu!mania (pre-#24109)`), all evaluated over the same **832-beatmap** dataset, so the rows are directly comparable; even where an algorithm has a narrower official design domain (Daniel, for instance), standard mode applies no dataset trimming.

Again, the composite scores are for horizontal reference only — **ISACC is the metric that truly matters**.

> **Dataset snapshot (2026-10-18)**: every number and ISACC below comes from the **832-chart standard batch**. The sample set has since changed to **855 charts** (24 dan courses added to the `course` family — 23 LN dan courses plus one REFORM FINAL-THETA, where `rc course` : `ln course` split the family weight 3:3 — and one retired chart dropped), so totals and ranks will drift slightly; the table will be refreshed in one pass after a full re-run. Until then, read it as an **832-chart snapshot**.

Abbreviations: **ACC** Ground-Truth Accuracy, **ISACC** In-Scope Accuracy, **SCP** Scope, **AIN** Anti-Rate-Inversion, **ASLN** Anti-Short-LN Inflation, **CT** Local Continuity, **SP** Computation Speed, **AJ** Anti-Jitter.

| Algorithm | CAB Score | Accuracy<br>(ACC) | In-Scope Accuracy<br>(ISACC) | Scope<br>(SCP) | Anti-Inversion<br>(AIN) | Anti-Short-LN<br>(ASLN) | Continuity<br>(CT) | SP | Anti-Jitter<br>(AJ) |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| [Arily](https://github.com/ppy-sb/rosu-pp) | **70.52** | 72.53 | — | — | **100.00** | 91.51 | **100.00** | **100.00** | 69.75 |
| [Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) | 69.28 | 73.01 | — | — | **100.00** | 88.60 | **100.00** | 60.46 | 69.72 |
| [Mixed](https://github.com/LeoBlackMT/osumania_map_analyser) | 58.88 | **73.50** | 93.53 | Excl. LN; 11 ≤ ND < 17 | 19.76 | **98.63** | 52.72 | 75.37 | 39.56 |
| [Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) | 51.62 | 61.03 | **96.60** | Excl. LN; 10.5 ≤ ND ≤ 16.5 | 74.85 | 49.27 | 91.83 | 73.24 | 44.28 |
| [osu-star-web (Treestar)](https://github.com/sky2002/osu-star-web) | 50.41 | 70.47 | — | — | 19.80 | 29.07 | 68.36 | 55.82 | 21.73 |
| [Daniel](https://github.com/TheBagelOfMan/Daniel) | 47.33 | 58.22 | 84.48 | Excl. LN; ND ≥ 10.5 | 91.55 | 0.00 | **100.00** | 74.48 | 62.49 |
| [Companella](https://github.com/LeoBlackMT/osumania_map_analyser) | 39.56 | 51.37 | 72.73 | Excl. LN; 2 < ND < 15 | 0.04 | 96.94 | 39.93 | 81.22 | 54.94 |
| [YAVSRG-Interlude](https://github.com/YAVSRG/Interlude) | 37.51 | 44.59 | — | — | **100.00** | 0.00 | **100.00** | **100.00** | **79.44** |
| [osu!lazer (Official)](https://github.com/ppy/osu) | 34.56 | 39.10 | — | — | **100.00** | 49.81 | **100.00** | 91.86 | 19.69 |
| [MinaCalc-0.72.3](https://github.com/etternagame/etterna) | 30.28 | 43.72 | 61.83 | Excl. LN | 9.74 | 0.00 | 79.22 | **100.00** | 24.82 |
| [osu!mania (pre-#24109)](https://github.com/ppy/osu) | 28.56 | 33.63 | — | — | **100.00** | 25.18 | **100.00** | 91.73 | 20.94 |

> **Note**: `osu!mania (pre-#24109)` is a **historical reference variant**, not a current algorithm: it is built from ppy/osu tag `2023.815.0`, i.e. the official mania algorithm **before** `ppy/osu#24109` (LN value scaling, merged 2023-08-17). It is listed to quantify what that LN fix changed — its LN charts read about **+0.32★** higher on average (up to +1.20★), while rice charts are essentially unchanged.

> **Note**: The `Star-Rating-Rebirth` row is run via the Rust port `sunny-rs`; its SP term inherits the original Python implementation's true speed (60.46) so as to reflect the algorithm itself rather than the port. Every other dimension is identical to the port's own measurements.

> **Note**: "Scope" is the applicability range an algorithm declares. It can come from two sources: an explicit `--declare-scope` at run time (Daniel / Dan-Overlay / Companella / MinaCalc), or from the algorithm's own implementation — Mixed's Roxy arm declares a numeric window of 11–17 and routes anything outside it back to the Azusa arm. "In-Scope Accuracy" is a 0–100 score converted with **exactly the same** scoring standard as ACC — i.e. calibration anchors are **re-fitted on the kept charts**, and the structural-inversion penalty is **recomputed from those in-scope anchors** (never inherited from the full run: once a declaration removes the charts responsible for a dip, the in-scope anchor sequence is usually close to monotonic, so reusing the full-run penalty would charge the same defect twice). Taken from the full 832-chart standard run. A declaration **never** changes any dimension score or the total above — those always span the full dataset. Algorithms without a declared scope are left blank (—).

### Evaluated Algorithms and Authors

| Algorithm | Author / Maintainer | Repository Link |
| :--- | :--- | :--- |
| **Arily** | [Ari Arimaniac (arily)](https://github.com/ppy-sb) | [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) |
| **Star-Rating-Rebirth** | [[Crz]sunnyxxy](https://github.com/sunnyxxy) (Rust port: TuRou) | [sunnyxxy/Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) |
| **Mixed** | [Leo_Black (LeoBlackMT)](https://github.com/LeoBlackMT) | [LeoBlackMT/osumania_map_analyser](https://github.com/LeoBlackMT/osumania_map_analyser) |
| **Dan-Overlay** | [acarranzao1a-png](https://github.com/acarranzao1a-png) | [acarranzao1a-png/Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) |
| **osu-star-web (Treestar)** | [sky2002](https://github.com/sky2002) | [sky2002/osu-star-web](https://github.com/sky2002/osu-star-web) |
| **Daniel** | [TheBagelOfMan](https://github.com/TheBagelOfMan) | [TheBagelOfMan/Daniel](https://github.com/TheBagelOfMan/Daniel) |
| **Companella** | [Leo_Black (LeoBlackMT)](https://github.com/LeoBlackMT) | [LeoBlackMT/osumania_map_analyser](https://github.com/LeoBlackMT/osumania_map_analyser) |
| **YAVSRG-Interlude** | [YAVSRG Team / kbackend](https://github.com/YAVSRG) | [YAVSRG/Interlude](https://github.com/YAVSRG/Interlude) |
| **osu!lazer (Official)** | [ppy / osu! team](https://github.com/ppy) | [ppy/osu](https://github.com/ppy/osu) |
| **MinaCalc-0.72.3** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **osu!mania (pre-#24109)** | [ppy / osu! team](https://github.com/ppy) (Historical reference, tag 2023.815.0) | [ppy/osu](https://github.com/ppy/osu) |

### Per-Algorithm Domain Strengths

Each algorithm's pattern strengths and weaknesses over the **all-range tier** are as follows (verdict rules in "Domain Fitness Profile"; ratio = pattern RMSE / median of per-pattern RMSE, lower is better; `—` means under-sampled or not judged):

| Algorithm | Strength | Adequate | Weakness |
| :--- | :--- | :--- | :--- |
| Arily | — | speed (0.86x)、stamina (1.18x)、tech (0.99x)、course (1.00x) | jack (1.48x)、ln (1.83x) |
| Star-Rating-Rebirth | — | speed (0.86x)、tech (0.98x)、course (1.00x) | jack (1.42x)、stamina (1.20x)、ln (1.75x) |
| Mixed | — | jack (0.81x)、speed (1.00x)、stamina (0.84x)、tech (0.85x) | ln (2.93x)、course (1.97x) |
| Dan-Overlay | — | jack (0.83x)、speed (1.00x)、stamina (0.99x)、tech (0.97x) | ln (6.60x)、course (1.71x) |
| osu-star-web (Treestar) | stamina (0.74x) | speed (0.92x)、tech (1.00x)、course (0.89x) | jack (1.22x)、ln (1.98x) |
| Daniel | jack (0.76x)、tech (0.67x) | speed (0.82x)、stamina (1.00x) | ln (4.13x)、course (1.42x) |
| Companella | — | jack (0.88x)、speed (0.92x)、stamina (0.99x)、tech (1.00x) | ln (1.66x)、course (1.56x) |
| YAVSRG-Interlude | — | jack (0.87x)、speed (0.87x)、stamina (1.17x)、tech (1.00x) | ln (3.87x)、course (1.87x) |
| osu!lazer (Official) | jack (0.77x) | speed (1.09x)、stamina (0.82x)、tech (1.00x) | ln (1.46x)、course (1.24x) |
| MinaCalc-0.72.3 | — | jack (0.91x)、speed (1.04x)、stamina (1.10x)、tech (1.00x)、course (0.95x) | ln (3.44x) |
| osu!mania (pre-#24109) | jack (0.68x)、stamina (0.72x) | speed (1.11x)、tech (1.00x)、course (1.08x) | ln (1.50x) |

> **Common observation**: nearly **every algorithm lists LN (ln) as its weakest pattern** — consistent with the fact that current 4K difficulty algorithms generally lack mature LN modelling. A few (osu!lazer, Daniel, Interlude, Mixed, Dan-Overlay) are also weak on dan courses (course); meanwhile `speed` and `tech` are the only two dimensions where every algorithm holds the adequate line.

### Domain Fitness Profile

The accuracy dimension is always computed over the **full dataset** and is never narrowed by filters. So that reports can still state *which domains an algorithm handles well and which it does not*, every report carries a **programmatically derived** domain-fitness profile — computed entirely from data, with no human-written prose, so it stays maintainable over time:

| `ratio = pattern RMSE / median of per-pattern RMSE` | Verdict |
| :---: | :---: |
| ≤ 0.80 | **Strength** |
| 0.80 – 1.20 | **Adequate** |
| > 1.20 | **Weakness** |
| sample count < 20 | Under-sampled (not judged) |

The profile is emitted into the "Pattern-Stratified Breakdown" table of the Markdown report (with sample counts, baseline ratio and verdict) and into the `domain_profile` field of the JSON.

### Difficulty Tiers (4-tier slicing)

| Tier | Range | Dimensions |
| :--- | :---: | :--- |
| All Range | ≥ -2.5 | All 6 dimensions |
| Number Dan | [-2.5, 10.5) | Static 4 (ACC / ASLN / SP / AJ) |
| Greek Dan | [10.5, 15.5] | Static 4 |
| Inhuman Dan | > 15.5 | Static 4 |

### Cross-Scale Calibration

**ND** is short for **Numeric Difficulty** — the continuous difficulty scale that expresses Greek-letter dan tiers (Alpha 11.0, Beta 12.0, …, Theta 18.0) as numeric values. It is the default ground-truth scale of this benchmark.

Non-ND-scale algorithms (e.g. Star Rating, MSD) are mapped into ND space via **cohort centroid anchoring + weighted isotonic regression (PAVA)**, so that calibrated RMSE is computed uniformly — with macro-tier inversion violations faithfully recorded.

### ACC Metric: Sub-Pattern Weighting (family weight × within-family 3:1)

The RMSE sub-score of the ACC dimension uses **two-level weighting**: fixed per-pattern (family) weights, then a within-family split across sub-patterns —

$$\text{RMSE}_{balanced} = \sum_{f} w_f \cdot \frac{\sum_{i \in f} r_i \cdot \text{RMSE}_{f,i}}{\sum_{i \in f} r_i}, \quad w = \{\text{jack } 0.20,\ \text{speed } 0.20,\ \text{stamina } 0.20,\ \text{tech } 0.20,\ \text{ln } 0.15,\ \text{course } 0.05,\ \text{unsigned } 0.05\}$$

Sub-patterns are written with **binomial ids** (`family-subpattern`, e.g. `tech-stream-tech`, `speed-unsigned`). Inside a family, every classified sub-pattern counts **3** and the `Unsigned` catch-all counts **1** ("classified : unsigned = 3 : 1"), re-normalised over the buckets actually present — e.g. for tech: `stream tech : jack tech : mix tech : Unsigned = 3 : 3 : 3 : 1`. The family weight therefore cannot be silently rewritten by "how many buckets a family happens to have", and each sub-pattern's contribution no longer depends on how many maps the library holds.

Both levels are a **weight-normalised arithmetic mean** of RMSEs — *not* a weighted root-mean-square ("square, weight, then root"). Patterns absent from the table fall back to `0.05`, the same level as `course`.

The global RMSE is a sample-count-weighted aggregate, so the sample distribution steers the score directly: a pattern with many maps (e.g. `jack`, 219) has its error amplified, while a pattern with few maps (e.g. `unsigned`, just 12) barely moves the total no matter how badly an algorithm fails on it. With **fixed family + sub-pattern weights**, each bucket contributes independently of how many maps the sample library happens to contain — removing the "farm the large-sample pattern" loophole and giving small-sample buckets the attention they deserve.

> **Note**: `unsigned` is the catch-all bucket for unclassified patterns, holding only 12 beginner-tier maps. It is pinned explicitly at the `course` level (0.05) so that a bucket carrying 1.5% of the samples cannot dominate the total through an inflated fallback weight; per-family `xxx-unsigned` buckets (e.g. `speed-unsigned`, 88 maps) are likewise weighted at 1.

## Black-Box Integration

The benchmark does exactly two things: it hands a `.osu` beatmap to the algorithm, and reads one difficulty value back from stdout. Algorithms written in any language (Python / C++ / Rust / C# / Node) can plug in as long as they follow the protocol — with zero changes to the benchmark itself:

```text
Benchmark → Algorithm: /path/to/beatmap.osu (or streamed beatmap text via stdin)
Algorithm → Benchmark: 14.852000 (a single-line difficulty value, or {"difficulty": 14.852})
```

```bash
# Run unit tests
pytest
```

## Repository Layout

```text
cab/                # Core benchmark package
├── parser/         # .osu parser and uuzs_bench.csv ground-truth loader
├── mutator/        # Rate scaling / Gaussian jitter / short-LN injection / HO probe
├── adapter/        # Algorithm adapters (CLI / stdio / built-in bridges)
├── metrics/        # Six-dimension metrics and isotonic calibration engine
├── runner/         # Multi-tier scheduling, filtering and multiprocessing
└── report/         # Markdown / JSON / charts / console reports + domain fitness profile
samples/4k/         # 4K sample set (855 beatmaps + uuzs_bench.csv ground truth, shipped with the repo)
tests/              # Unit tests
```

Pattern categories in the sample set: jack, ln, speed, stamina, tech, plus a course (dan course) directory and an `unsigned` directory for unclassified beginner-tier singles (pattern recorded as `Unsigned`).

## Outputs

Each run writes into the output directory (default `results/`):

- `CAB_MULTI_TIER_SUMMARY.md`: cross-tier comparison report (with a domain-fitness profile per tier);
- `CAB_BENCHMARK_REPORT.md`: detailed all-range report (with pattern breakdown and domain-fitness profile);
- `cab_multi_tier_summary.json` / `cab_benchmark_result.json`: machine-readable results (with `domain_profile`);
- `predictions.csv`: per-map prediction breakdown (written by default; includes the three declared-scope columns — see Usage Guide §6.3);
- diagnostic charts: radar chart, calibration curve, inversion profiles, tier comparison.

## Documents

- [Usage & Debugging Guide](./CAB_VA_USAGE_GUIDE_EN.md)
