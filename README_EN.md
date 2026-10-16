# CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm

**A standardized consistency & accuracy benchmark for vertical-scrolling rhythm game difficulty algorithms**

**中文版**: [README.md](./README.md)

CAB-VA is a standardized black-box benchmarking framework for difficulty rating algorithms in vertical-scrolling rhythm games (VSRG, currently focused on osu!mania 4K). Comparable to ARC-AGI-2 in the AI world, it throws difficulty algorithms into a "harsh crucible" of rate scaling, micro-noise and temporal edge cases, systematically exposing four classes of counter-intuitive flaws:

- **Rate Inversion**: at higher rates the physical demands grow, yet the algorithm's output drops instead;
- **Cliffing**: hard thresholds make difficulty leap or crash under tiny perturbations;
- **Jitter Sensitivity**: a few milliseconds of timing offset cause wild rating swings;
- **Short LN Inflation**: 20ms–100ms short LNs are treated as full-length holds, causing inflation.

## Why CAB-VA (A Word from the Author)

Mania difficulty algorithms are multiplying fast these days — no longer limited to developer-led projects like MSD or Interlude, or long-labored reworks like SunnyXXY Rework that take years to forge. Many more targeted, faster-iterating algorithms such as Companella, Daniel and Dan-Overlay are now springing up.

Training approaches are expanding too — no longer just building "secondary algorithms" with linear models or decision trees fit to a training set. Arily, the owner of the ppy.sb server, pioneered a surprisingly solid pp system built from a mountain of replays, and more players will likely follow that path. A flourishing scene, everything blooming at once…

I believe the community is increasingly hungry for a more authoritative way to show just how badass their algorithm is.

So here it is — Consistency and Accuracy Bench for VSRG Algorithm, CAB-VA for short — fumbled into the world by yours truly, a retired, invisible small-timer. Call it whatever you like.

> **Note**: Please don't act like certain LLMs and just hammer this benchmark with decision trees to farm a high score. That's textbook overfitting, and you'll never get your algorithm back into shape afterwards.

*The rest of this document was mostly written by AI. Bear with it.*

## Core Design

### Black-Box I/O Contract

The benchmark does exactly two things: it hands a `.osu` beatmap to the algorithm, and reads one difficulty value back from stdout. Algorithms written in any language (Python / C++ / Rust / C# / Node) can plug in as long as they follow the protocol — with zero changes to the benchmark itself:

```text
Benchmark → Algorithm: /path/to/beatmap.osu (or streamed beatmap text via stdin)
Algorithm → Benchmark: 14.852000 (a single-line difficulty value, or {"difficulty": 14.852})
```

### Evaluation Dimensions (6 dimensions, 100 points total)

| Dimension | Weight | Key metrics |
| :--- | :---: | :--- |
| Ground-Truth Accuracy (ACC) | 40% | Calibrated ng-RMSE, MAE, Spearman ρ, within-tier dispersion (WTD) |
| Anti-Rate-Inversion (AIN) | 20% | Monotonicity under 1.01x–2.0x rate scaling |
| Anti-Short-LN Inflation (ASLN) | 15% | 20ms–100ms short-LN injection + black-box HO probe |
| Local Continuity (CT) | 10% | High-density micro-stepping + local Lipschitz cliff detection |
| Computation Speed (Speed) | 5% | Compute cost in ms/s |
| Anti-Jitter (AJ) | 10% | σ = 8ms per-lane order-preserving Gaussian timing jitter |

> **For most players and algorithm authors, the composite score is just for fun — Accuracy is the one that truly matters; the other five are the cherry on top.**

### Difficulty Tiers (4-tier slicing)

| Tier | Range | Dimensions |
| :--- | :---: | :--- |
| All Range | ≥ -2.5 | All 6 dimensions |
| Number Dan | [-2.5, 10.5) | Static 4 (ACC / ASLN / Speed / AJ) |
| Greek Dan | [10.5, 15.5] | Static 4 |
| Inhuman Dan | > 15.5 | Static 4 |

### Cross-Scale Calibration

Non-ng-scale algorithms (e.g. Star Rating, MSD) are mapped into ng space via **cohort centroid anchoring + weighted isotonic regression (PAVA)**, so that calibrated RMSE is computed uniformly — with macro-tier inversion violations faithfully recorded.

### Domain Fitness Profile

The accuracy dimension is always computed over the **full dataset** and is never narrowed by filters. So that reports can still state *which domains an algorithm handles well and which it does not*, every report carries a **programmatically derived** domain-fitness profile — computed entirely from data, with no human-written prose, so it stays maintainable over time:

| `ratio = pattern RMSE / median of per-pattern RMSE` | Verdict |
| :---: | :---: |
| ≤ 0.80 | **Strength** |
| 0.80 – 1.20 | **Adequate** |
| > 1.20 | **Weakness** |
| sample count < 20 | Under-sampled (not judged) |

The profile is emitted into the "Pattern-Stratified Breakdown" table of the Markdown report (with sample counts, baseline ratio and verdict) and into the `domain_profile` field of the JSON. A companion tool can also backfill the profile into **existing results** without re-running any algorithm (see Usage Guide §7.3).

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

Built-in algorithm bridges: `daniel`, `mixed`, `minacalc`, `companella`, `dan-overlay`, `sunny`, `treestar`, `interlude` (cross-benchmark results live under the `results_*` directories).

There are also two persistent Rust implementations:

- `sunny-rs` — the older version of [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) (commit `f0663cf`, "implement sunnyxxy formula"), a high-fidelity Rust port of Star-Rating-Rebirth (Sunny Rework). On original beatmaps it matches the original Python implementation bit-for-bit (~1e-14), yet runs roughly **40× faster**, so the standard batch uses it in place of the original `sunny` while still reporting it as **Star-Rating-Rebirth**.
- `arily` — the mainline [ppy-sb/rosu-pp](https://github.com/ppy-sb/rosu-pp) (a modified version by Arily, server owner of ppy.sb), evaluated as an independent algorithm.

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
samples/4k/         # 4K sample set (787 beatmaps + uuzs_bench.csv ground truth)
tests/              # Unit tests
scripts/            # Helper batch scripts
tools/              # Helper tools (e.g. domain-profile backfill for old results)
_archive/           # Archived historical result snapshots (read-only, not part of evaluation)
```

Pattern categories in the sample set: jack, ln, speed, stamina, tech, plus a course (dan course) directory and an `unsigned` directory for unclassified beginner-tier singles (pattern recorded as `Unsigned`).

## Outputs

Each run writes into the output directory (default `results/`):

- `CAB_MULTI_TIER_SUMMARY.md`: cross-tier comparison report (with a domain-fitness profile per tier);
- `CAB_BENCHMARK_REPORT.md`: detailed all-range report (with pattern breakdown and domain-fitness profile);
- `cab_multi_tier_summary.json` / `cab_benchmark_result.json`: machine-readable results (with `domain_profile`);
- diagnostic charts: radar chart, calibration curve, inversion profiles, tier comparison.

## Documents

- [Project Plan & Technical Specification](./CAB_VA_PROJECT_PLAN_EN.md)
- [Usage & Debugging Guide](./CAB_VA_USAGE_GUIDE_EN.md)

## Terminology Note

English documents use the standard community terms directly (jack, LN, pattern, rate scaling, dan, MSD, etc.). The Chinese documents and reports follow the [vsrg-en-to-zh-localization-reference](https://github.com/inuiyumegan/vsrg-en-to-zh-localization-reference) glossary, which maps: 叠键 (jack), 面条 (LN), 乱键 (speed), 耐力 (stamina), 技巧 (tech), 段位 (dan). Directory names, filenames and CLI flags always stay in English (`samples/4k/jack/...`, `--patterns speed,jack`).
