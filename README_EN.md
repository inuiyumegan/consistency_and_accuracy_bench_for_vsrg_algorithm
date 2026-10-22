# CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm

**A standardized consistency & accuracy benchmark for vertical-scrolling rhythm game difficulty algorithms**

**中文版**: [README.md](./README.md)

CAB-VA is a standardized black-box benchmarking framework for difficulty rating algorithms in vertical-scrolling rhythm games (VSRG, currently focused on osu!mania 4K). Comparable to ARC-AGI-2 in the AI world, it throws difficulty algorithms into a "harsh crucible" of rate scaling, micro-noise and temporal edge cases, systematically exposing four classes of counter-intuitive flaws:

- **Rate Inversion**: at higher rates the physical demands grow, yet the algorithm's output drops instead;
- **Cliffing**: hard thresholds make difficulty leap or crash under tiny perturbations;
- **Jitter Sensitivity**: a few milliseconds of timing offset cause wild rating swings;
- **Short LN Inflation**: 20ms–100ms short LNs are treated as full-length holds, causing inflation.

## Why CAB-VA (A Word from the Author)

Mania difficulty algorithms are multiplying fast these days — no longer limited to developer-led projects like MSD or [Interlude](https://github.com/YAVSRG/Interlude), or long-labored reworks like [SunnyXXY Rework](https://github.com/sunnyxxy/Star-Rating-Rebirth) that take years to forge. Many more targeted, faster-iterating algorithms such as [Companella](https://github.com/leyna64/companella), [Daniel](https://github.com/TheBagelOfMan/Daniel) and [Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) are now springing up.

Training approaches are expanding too — no longer just building "secondary algorithms" with linear models or decision trees fit to a training set. Arily ([Arily](https://github.com/arily)), the owner of the ppy.sb server, pioneered a surprisingly solid pp system built from a mountain of replays, and more players will likely follow that path. A flourishing scene, everything blooming at once…

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
| Ground-Truth Accuracy (ACC) | **backbone** | Sub-pattern-weighted ND-RMSE (pattern weight × within-pattern 3:1), MAE, Spearman ρ, within-tier dispersion (WTD); includes the structural-inversion penalty |
| Anti-Rate-Inversion (AIN) | 15% | Monotonicity under 1.0x–2.0x rate scaling |
| Anti-Short-LN Inflation (ASLN) | 15% | 49–59 ms short-LN injection; **equivalent-rate deviation** (target r\* ≈ 1.0, symmetric) + **continuous Hold-Off gate** (LN-blind / brute-force tap stripping gated to 0) |
| Local Continuity (CT) | 10% | High-density micro-stepping + local Lipschitz cliff detection |
| Computation Speed (SP) | 5% | Same-session ratio vs MinaCalc (33 dan-course probe, serial; Hill curve: 100 = instant completion, never reachable) |
| Anti-Jitter (AJ) | 5% | **OD-adaptive microscopic jitter** (9–11ms within hit window) + **intra-window arrangement sensitivity probe gate** (penalising brute-force density algorithms) |

> **Note**: as of 2026-10-17 the total is no longer a weighted average but **ACC backbone × per-dimension discounts**:
> $$\text{Total} = \text{ACC}\times\prod_i\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$
> "Discount β" means **how much of the total is lost if that dimension scores 0** — not a weight: **a perfect score adds nothing** (so saturated dimensions such as CT, where 7 of 10 algorithms hit exactly 100, no longer inflate every total), and **a weak dimension cannot be bought back by a strong one**. The ACC dimension also carries the **structural-inversion ("倒挂") penalty** (multi-scale dip severity, capped at −30 points; see "ACC Metric: Sub-Pattern Weighting"). Sub-tiers evaluate only ACC/ASLN/AJ (Speed is a global, once-measured metric); the dynamic dimensions simply drop out of the product (no renormalisation required).

## Cross-Algorithm Scoreboard

The table below is the **standard-mode, all-range** cross-benchmark: **fourteen current algorithms plus one historical reference variant** (`osu!mania (pre-#24109)`), all evaluated over the same **856-beatmap** dataset, so the rows are directly comparable; even where an algorithm has a narrower official design domain (Daniel, for instance), standard mode applies no dataset trimming.

Again, the composite scores are for horizontal reference only — **ISACC is the metric that truly matters**.

> **Dataset snapshot (2026-10-21, refreshed)**: every number below, ISACC included, comes from **one 856-chart batch** — the 16 algorithms from their standard runs, and ISACC re-derived by `selfcheck/scripts/refresh_isacc.py` from each run's `predictions.csv` (anchors re-fitted in scope, inversion penalty recomputed in scope). "Excl. LN" excludes LN content, i.e. `pattern = ln` *and* the LN dan courses.

Abbreviations: **ACC** Ground-Truth Accuracy, **ISACC** In-Scope Accuracy, **SCP** Scope, **AIN** Anti-Rate-Inversion, **ASLN** Anti-Short-LN Inflation, **CT** Local Continuity, **SP** Computation Speed, **AJ** Anti-Jitter.

| Algorithm | CAB Score | Accuracy<br>(ACC) | In-Scope Accuracy<br>(ISACC) | Scope<br>(SCP) | Anti-Inversion<br>(AIN) | Anti-Short-LN<br>(ASLN) | Continuity<br>(CT) | SP | Anti-Jitter<br>(AJ) |
| :--- | :---: | :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: |
| [Arily](https://github.com/ppy-sb/rosu-pp) | **77.92** | 82.67 | — | — | 99.91 | 76.06 | **100.00** | 80.14 | 75.12 |
| [Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) | 76.91 | **83.02** | — | — | **100.00** | 71.06 | **100.00** | 60.97 | **75.31** |
| [Mixed](https://github.com/LeoBlackMT/osumania_map_analyser) | 67.13 | 79.33 | 97.03 | Excl. LN; 11 ≤ ND < 17 | 53.82 | 89.45 | 54.66 | 69.46 | 65.27 |
| [osu-star-web (Treestar)](https://github.com/sky2002/osu-star-web) | 60.17 | 76.53 | — | — | 54.66 | 43.47 | 72.53 | 57.69 | 36.57 |
| [Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) | 58.30 | 63.57 | **97.57** | Excl. LN; 10.5 ≤ ND ≤ 16.5 | 84.74 | **93.78** | 78.02 | 67.01 | 69.80 |
| [Daniel](https://github.com/TheBagelOfMan/Daniel) | 53.23 | 65.03 | 90.58 | Excl. LN; ND ≥ 10.5 | 93.77 | 0.00 | **100.00** | 69.13 | 74.70 |
| [Companella](https://github.com/leyna64/companella) | 52.49 | 67.43 | 88.07 | Excl. LN; 2 < ND < 15 | 20.25 | 77.03 | 42.35 | 75.87 | 67.30 |
| [osu!mania (holds-fix)](https://github.com/molneya/osu/tree/holds-fix) | 52.39 | 59.05 | — | — | **100.00** | 60.35 | **100.00** | 77.16 | 8.52 |
| [YAVSRG-Interlude](https://github.com/YAVSRG/Interlude) | 48.38 | 59.44 | — | — | **100.00** | 0.00 | **100.00** | 83.79 | 30.63 |
| [osu!lazer (Official)](https://github.com/ppy/osu) | 47.39 | 56.10 | — | — | **100.00** | 30.02 | **100.00** | 76.94 | 9.59 |
| [MinaCalc-0.68.0](https://github.com/etternagame/etterna) | 46.74 | 58.77 | 80.98 | Excl. LN | 92.14 | 0.00 | 87.40 | **86.27** | 30.94 |
| [MinaCalc-0.70.0](https://github.com/etternagame/etterna) | 45.31 | 56.57 | 76.64 | Excl. LN | 98.69 | 0.00 | 96.55 | 83.19 | 11.01 |
| [MinaCalc-0.72.3](https://github.com/etternagame/etterna) | 40.25 | 54.43 | 73.08 | Excl. LN | 46.98 | 0.00 | 72.65 | 84.80 | 58.48 |
| [osu!mania (pre-#24109)](https://github.com/ppy/osu) | 39.98 | 48.00 | — | — | **100.00** | 21.71 | **100.00** | 77.40 | 9.03 |
| [MinaCalc-0.74.0](https://github.com/etternagame/etterna) | 36.74 | 50.27 | 68.13 | Excl. LN | 25.27 | 0.00 | 96.16 | 84.66 | 59.28 |
| [MinaCalc-0.75.0](https://github.com/etternagame/etterna) | 32.21 | 43.96 | 58.94 | Excl. LN | 26.67 | 0.00 | 95.22 | 84.51 | 61.79 |

> **Note**: `osu!mania (pre-#24109)` is a **historical reference variant**, not a current algorithm: it is built from ppy/osu tag `2023.815.0`, i.e. the official mania algorithm **before** `ppy/osu#24109` (LN value scaling, merged 2023-08-17). It is listed to quantify what that LN fix changed — its LN charts read about **+0.32★** higher on average (up to +1.20★), while rice charts are essentially unchanged.
>
> "Scope" is the applicability range an algorithm declares. It can come from two sources: an explicit `--declare-scope` at run time (Daniel / Dan-Overlay / Companella / MinaCalc), or from the algorithm's own implementation — Mixed's Roxy arm declares a numeric window of 11–17 and routes anything outside it back to the Azusa arm. "In-Scope Accuracy" is a 0–100 score converted with **exactly the same** scoring standard as ACC — i.e. calibration anchors are **re-fitted on the kept charts**, and the structural-inversion penalty is **recomputed from those in-scope anchors** (never inherited from the full run: once a declaration removes the charts responsible for a dip, the in-scope anchor sequence is usually close to monotonic, so reusing the full-run penalty would charge the same defect twice). Taken from the same 856-chart standard batch as the table above. A declaration **never** changes any dimension score or the total above — those always span the full dataset. Algorithms without a declared scope are left blank (—).

### Raw metrics (pre-normalisation)

> The raw measurements behind the six dimensions above, before any 0-100 conversion. Reference only — no score is computed from these numbers directly. ↓ = lower is better, ↑ = higher is better.
> 
> **The ND-RMSE column is the sub-pattern weighted RMSE the score actually uses** (JSON `weighted_stratified_rmse`). It is **not** the "calibrated ND-RMSE" quoted in the per-run report (global RMSE, JSON `calibrated_rmse`): the former is normalised over fixed pattern weights and a within-pattern 3:1 split, the latter is the global root-mean-square of per-chart error.
> 
> **The speed column** is the **serial-probe median over 33 dan tiers** (JSON `median_speed_ms_per_s`), the same value the score uses; it is not an average over all 856 charts and does not depend on `--workers`.

| Algorithm | ND-RMSE ↓ | MAE ↓ | Spearman ρ ↑ | Inversion idx ↓ | ITR % (diagnostic) | Short-LN RMSE ↓ | Roughness ↓ | Speed ms/s ↓ | Jitter RMSE ↓ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| Arily | 0.6778 | 0.470 | 0.983 | 0.00 | 6.9 | 0.440 | 0.071 | 0.0960 | 0.185 |
| Star-Rating-Rebirth | 0.6631 | 0.462 | 0.983 | 0.00 | 7.3 | 0.459 | 0.070 | 2.0970 | 0.185 |
| Mixed | 0.7514 | 0.497 | 0.980 | 0.10 | 4.8 | 0.736 | 0.431 | 0.5288 | 0.700 |
| osu-star-web (Treestar) | 0.8245 | 0.627 | 0.970 | 0.04 | 17.1 | 0.882 | 0.415 | 6.1276 | 0.682 |
| Dan-Overlay | 0.9238 | 0.668 | 0.955 | 0.14 | -1.1 | 0.585 | 0.281 | 0.7014 | 0.539 |
| Daniel | 1.0657 | 0.739 | 0.954 | 0.01 | 0.0 | 0.000 | 0.082 | 0.5991 | 0.449 |
| Companella | 1.0758 | 0.672 | 0.957 | 0.18 | 4.2 | 0.621 | 0.489 | 0.1661 | 0.499 |
| osu!mania (holds-fix) | 1.1590 | 0.985 | 0.917 | 0.00 | 13.9 | 1.262 | 0.015 | 0.1167 | 0.054 |
| YAVSRG-Interlude | 1.1604 | 0.944 | 0.932 | 0.00 | 0.0 | 0.000 | 0.038 | 0.0297 | 0.099 |
| osu!lazer (Official) | 1.1964 | 1.027 | 0.909 | 0.00 | 17.7 | 1.484 | 0.015 | 0.1190 | 0.052 |
| MinaCalc-0.68.0 | 1.1339 | 0.863 | 0.938 | 0.03 | 0.0 | 0.000 | 0.136 | 0.0163 | 3.226 |
| MinaCalc-0.70.0 | 1.2334 | 0.903 | 0.932 | 0.13 | 0.0 | 0.000 | 0.114 | 0.0337 | 3.584 |
| MinaCalc-0.72.3 | 1.2512 | 0.996 | 0.922 | 0.12 | 0.0 | 0.000 | 0.444 | 0.0207 | 2.968 |
| osu!mania (pre-#24109) | 1.4109 | 1.170 | 0.896 | 0.00 | 22.4 | 1.681 | 0.016 | 0.1172 | 0.054 |
| MinaCalc-0.74.0 | 1.3334 | 1.084 | 0.909 | 0.14 | 0.0 | 0.000 | 0.226 | 0.0206 | 3.020 |
| MinaCalc-0.75.0 | 1.4339 | 1.210 | 0.879 | 0.13 | 0.0 | 0.000 | 0.245 | 0.0233 | 2.808 |

### Evaluated Algorithms and Authors

| Algorithm | Author / Maintainer | Repository Link |
| :--- | :--- | :--- |
| **Arily** | [arily](https://github.com/arily) | [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) |
| **Star-Rating-Rebirth** | [[Crz]sunnyxxy](https://github.com/sunnyxxy) (Rust port: [TuRou](https://github.com/TrueRou)) | [sunnyxxy/Star-Rating-Rebirth](https://github.com/sunnyxxy/Star-Rating-Rebirth) |
| **Mixed** | [Leo_Black (LeoBlackMT)](https://github.com/LeoBlackMT) | [LeoBlackMT/osumania_map_analyser](https://github.com/LeoBlackMT/osumania_map_analyser) |
| **osu-star-web (Treestar)** | [sky2002](https://github.com/sky2002) | [sky2002/osu-star-web](https://github.com/sky2002/osu-star-web) |
| **Dan-Overlay** | [acarranzao1a-png](https://github.com/acarranzao1a-png) | [acarranzao1a-png/Dan-Overlay](https://github.com/acarranzao1a-png/Dan-Overlay) |
| **Daniel** | [TheBagelOfMan](https://github.com/TheBagelOfMan) | [TheBagelOfMan/Daniel](https://github.com/TheBagelOfMan/Daniel) |
| **osu!mania (holds-fix)** | [molneya](https://github.com/molneya) (Historical branch, LN fix) | [molneya/osu](https://github.com/molneya/osu/tree/holds-fix) |
| **Companella** | [Leyna (leyna64)](https://github.com/leyna64) | [leyna64/companella](https://github.com/leyna64/companella) |
| **MinaCalc-0.68.0** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **YAVSRG-Interlude** | [YAVSRG Team / kbackend](https://github.com/YAVSRG) | [YAVSRG/Interlude](https://github.com/YAVSRG/Interlude) |
| **MinaCalc-0.70.0** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **MinaCalc-0.72.3** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **osu!lazer (Official)** | [ppy / osu! team](https://github.com/ppy) | [ppy/osu](https://github.com/ppy/osu) |
| **MinaCalc-0.74.0** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |
| **osu!mania (pre-#24109)** | [ppy / osu! team](https://github.com/ppy) (Historical reference, tag 2023.815.0) | [ppy/osu](https://github.com/ppy/osu) |
| **MinaCalc-0.75.0** | [etternagame](https://github.com/etternagame) | [etternagame/etterna](https://github.com/etternagame/etterna) |

### Per-Algorithm Domain Strengths

Each algorithm's pattern strengths and weaknesses over the **all-range tier** are as follows (verdict rules in "Domain Fitness Profile"; ratio = pattern RMSE / median of per-pattern RMSE, lower is better; `—` means under-sampled or not judged):

| Algorithm | Strength | Adequate | Weakness |
| :--- | :--- | :--- | :--- |
| Arily | stamina (0.70x) | jack (1.02x)、speed (0.83x)、tech (1.00x)、course (1.11x) | ln (1.47x) |
| Star-Rating-Rebirth | stamina (0.73x) | jack (1.03x)、speed (0.82x)、tech (1.00x)、course (1.08x) | ln (1.43x) |
| Mixed | — | jack (0.81x)、speed (1.00x)、stamina (0.83x)、tech (0.85x) | ln (2.90x)、course (1.94x) |
| osu-star-web (Treestar) | speed (0.75x)、stamina (0.67x) | jack (1.11x)、tech (0.85x)、course (1.14x) | ln (1.42x) |
| Dan-Overlay | — | jack (0.82x)、speed (1.00x)、stamina (0.98x)、tech (0.96x) | ln (6.56x)、course (4.09x) |
| Daniel | jack (0.76x)、tech (0.68x) | speed (0.82x)、stamina (1.00x) | ln (4.14x)、course (2.02x) |
| Companella | — | jack (0.86x)、speed (0.80x)、stamina (0.94x)、tech (1.00x) | ln (1.71x)、course (1.52x) |
| osu!mania (holds-fix) | — | jack (0.92x)、speed (1.12x)、stamina (0.92x)、tech (1.00x) | ln (1.53x)、course (1.49x) |
| YAVSRG-Interlude | — | jack (1.00x)、speed (0.97x)、tech (0.98x) | stamina (1.32x)、ln (3.78x)、course (2.07x) |
| osu!lazer (Official) | — | jack (0.81x)、speed (1.10x)、stamina (0.82x)、tech (1.00x) | ln (1.46x)、course (1.48x) |
| MinaCalc-0.68.0 | — | jack (1.08x)、speed (1.00x)、stamina (0.96x)、tech (1.00x) | ln (4.03x)、course (2.09x) |
| MinaCalc-0.70.0 | jack (0.80x) | speed (0.88x)、tech (0.84x) | stamina (1.45x)、ln (3.55x)、course (1.69x) |
| MinaCalc-0.72.3 | — | jack (0.81x)、speed (1.00x)、stamina (1.17x)、tech (0.92x) | ln (3.14x)、course (1.60x) |
| osu!mania (pre-#24109) | jack (0.62x)、stamina (0.60x) | speed (1.02x)、tech (1.00x) | ln (1.56x)、course (1.71x) |
| MinaCalc-0.74.0 | tech (0.74x) | jack (1.00x)、speed (0.82x)、stamina (1.03x) | ln (2.54x)、course (1.31x) |
| MinaCalc-0.75.0 | speed (0.77x)、tech (0.71x) | jack (1.13x)、stamina (1.00x)、course (1.14x) | ln (2.24x) |

> **Common observation**: **all 16 algorithms** list LN (ln) as their weakest pattern (1.42-6.56x), and since the 24 LN dan courses joined the `course` pattern, **dan courses are a weakness for 12 of 16** (1.31-4.09x; only Arily, Star-Rating-Rebirth, Treestar and MinaCalc-0.75.0 hold). `speed` and `jack` are held by every algorithm, as is `tech` (0.68-1.00x).

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
| Number Dan | [-2.5, 10.5) | Static 3 (ACC / ASLN / AJ) |
| Greek Dan | [10.5, 15.5] | Static 3 |
| Inhuman Dan | > 15.5 | Static 3 |

### Cross-Scale Calibration

**ND** is short for **Numeric Difficulty** — the continuous difficulty scale that expresses Greek-letter dan tiers (Alpha 11.0, Beta 12.0, …, Theta 18.0) as numeric values. It is the default ground-truth scale of this benchmark.

Non-ND-scale algorithms (e.g. Star Rating, MSD) are mapped into ND space via **cohort centroid anchoring + weighted isotonic regression (PAVA)**, so that calibrated RMSE is computed uniformly — with macro-tier inversion violations faithfully recorded.

### ACC Metric: Sub-Pattern Weighting (pattern weight × within-pattern 3:1)

The RMSE sub-score of the ACC dimension uses **two-level weighting**: fixed per-pattern weights, then a within-pattern split across sub-patterns —

$$\text{RMSE}_{balanced} = \sum_{f} w_f \cdot \frac{\sum_{i \in f} r_i \cdot \text{RMSE}_{f,i}}{\sum_{i \in f} r_i}, \quad w = \{\text{jack } 0.20,\ \text{speed } 0.20,\ \text{stamina } 0.20,\ \text{tech } 0.20,\ \text{ln } 0.15,\ \text{course } 0.05,\ \text{unsigned } 0.05\}$$

Inside a pattern, every classified sub-pattern counts **3** and the `Unsigned` catch-all counts **1** ("classified : unsigned = 3 : 1"), re-normalised over the buckets actually present — e.g. for tech: `stream tech : jack tech : mix tech : Unsigned = 3 : 3 : 3 : 1`. The pattern weight therefore cannot be silently rewritten by "how many buckets a pattern happens to have", and each sub-pattern's contribution no longer depends on how many maps the library holds.

Both levels are a **weight-normalised arithmetic mean** of RMSEs.

The global RMSE is a sample-count-weighted aggregate, so the sample distribution steers the score directly: a pattern with many maps (e.g. `jack`, 219) has its error amplified, while a pattern with few maps (e.g. `unsigned`, just 12) barely moves the total no matter how badly an algorithm fails on it. With **fixed pattern + sub-pattern weights**, each bucket contributes independently of how many maps the sample library happens to contain — removing the "farm the large-sample pattern" loophole and giving small-sample buckets the attention they deserve.

> **Note**: `unsigned` is the catch-all bucket for unclassified patterns, holding only 12 beginner-tier maps. It is pinned explicitly at the `course` level (0.05) so that a bucket carrying 1.5% of the samples cannot dominate the total through an inflated fallback weight; per-pattern `xxx-unsigned` buckets (e.g. `speed-unsigned`, 88 maps) are likewise weighted at 1.

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
samples/4k/         # 4K sample set (856 beatmaps + uuzs_bench.csv ground truth, shipped with the repo)
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

## Acknowledgements

Part of the chart-difficulty ground truth was contributed by:

| Contributor | Contribution |
| :--- | :--- |
| [晓泽2718](https://osu.ppy.sh/users/24530137) | Difficulties for part of the number-dan **jack** charts |
| [未来lk](https://osu.ppy.sh/users/24038353) | Difficulties for part of the charts |

## Documents

- [Usage & Debugging Guide](./CAB_VA_USAGE_GUIDE_EN.md)
