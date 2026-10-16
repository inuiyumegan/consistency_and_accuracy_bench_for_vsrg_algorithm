# CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm — User & Debugging Guide

**中文版**: [CAB_VA_USAGE_GUIDE.md](./CAB_VA_USAGE_GUIDE.md)

---

## 1. System Overview & Evaluation System

CAB-VA is an industrial-grade, multi-dimensional quantitative benchmark framework for VSRG difficulty algorithms (osu!mania, Malody, BMS, Etterna, etc.). It covers six core dimensions (each scored out of 100; total 100):

1. **Dimension 1: Ground-Truth Accuracy & Calibration (weight 40%)**
   - Checks rating accuracy against the benchmark map library: calibrated ND-RMSE, global MAE, Spearman rank correlation ρ, and within-tier dispersion (WTD).
   - **RMSE metric**: the RMSE sub-score of the accuracy dimension uses a **pattern-balanced weighted RMSE** (jack/speed/stamina/tech 0.20 each, ln 0.15, course 0.05) rather than a sample-count-weighted global RMSE, so that large-sample patterns cannot dominate the total (see "ACC Metric: Pattern-Balanced Weighting" in the README).
   - **Sample rule**: includes marathon courses and dan-course sets (`/samples/4k/course`) plus regular single maps by default.
2. **Dimension 2: Anti-Rate-Inversion / Monotonicity (weight 20%)**
   - Verifies strict non-decreasing behavior of difficulty output as clock rate increases (1.01x ~ 2.0x).
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**; physical clock transforms are applied only to rice-only single maps.
3. **Dimension 3: Anti-Short LN Release-Inflation Robustness (weight 15%)**
   - Converts regular rice notes into 20ms~100ms short LNs (release LNs) to test whether an algorithm is immune to "inflation attacks" introduced by mapper release timing.
   - **Anti-Stripping Rule**:
     - **LN folder exclusion penalty**: if the tester excludes the `ln` folder, the algorithm is deemed incapable of LN processing, and **the anti-short-LN score is forced to 0.0**.
     - **HO (Hold Off) probe**: if the difficulty delta between LN originals and HO (LN-to-tap) variants is < 2.0%, i.e. brute-force LN-to-tap stripping is detected, **the anti-short-LN score is forced to 0.0**.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.
4. **Dimension 4: Local Continuity & Cliffing (weight 10%)**
   - Computes the empirical local Lipschitz constant along rate curves, detecting and penalizing cliff jumps caused by hard step thresholds.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.
5. **Dimension 5: Computation Speed & Cost (weight 5%)**
   - Evaluates compute cost in ms/s (compute milliseconds per music second). Industrial reference scale: 0.1 ms/s → 100 points; each 10x slowdown deducts 25 points.
   - **Sample rule**: the standard benchmark map set.
6. **Dimension 6: Anti-Jitter Perturbation Robustness (weight 10%)**
   - Applies per-lane order-preserving (collision-free) Gaussian timing jitter (σ = 8ms, max = 15ms) to detect hypersensitivity or singular-point rating oscillations.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.

Weight priority:

- **All Range (6 dimensions)**:
  $$\text{ACC (40\%)} > \text{AIN (20\%)} > \text{ASLN (15\%)} > \text{CT (10\%)} = \text{AJ (10\%)} > \text{Speed (5\%)} \quad (\text{100\% total})$$
- **Sub-tiers (Number / Greek / Inhuman, 4 dimensions)**:
  Dynamic rate dimensions (AIN 20%, CT 10%) do not participate in sub-tier scoring (rate scaling necessarily crosses static difficulty boundaries). The remaining 4 dimensions are renormalized to 100:
  $$\text{ACC (57.14\%)} > \text{ASLN (21.43\%)} > \text{Speed (7.14\%)} > \text{AJ (14.29\%)} \quad (\text{100\% total})$$

---

## 2. Quick Start

### 2.1 Environment

Python 3.10+; core dependencies include `numpy`, `scipy`, `pandas`, `matplotlib`, `rich`. Run the entry script from the project root:

```bash
# Show all available options
./cab_run.sh run --help

# Run the full unit-test suite
pytest
```

### 2.2 Quick Mode

```bash
./cab_run.sh run --algorithm daniel --scale daniel --mode quick --output-dir results_quick
```

### 2.3 Standard Full Benchmark

```bash
./cab_run.sh run --algorithm daniel --scale daniel --mode standard --output-dir results
```

---

## 3. Dataset Filtering & Debugging

### 3.1 Why filter maps?

Many difficulty algorithms have specific design target domains:

- **Rice-only algorithms** (e.g. Daniel): no release/tail-overlap model for LN tails; feeding LN maps inevitably causes score underestimation and metric pollution.
- **Specific difficulty-range algorithms**: e.g. official calibration only covers high dan tiers (Daniel: Alpha 11.5 to Theta 18.5, i.e. ND ∈ [10.5, 18.5]); tiny perturbations of ND 1.0~5.0 easy maps are not part of the design goals.

Filtering isolates external noise and precisely diagnoses an algorithm's true accuracy and consistency **within its own design domain**.

### 3.2 Filtering CLI Reference

| Flag | Type | Description | Example |
| :--- | :--- | :--- | :--- |
| **`--patterns`** | comma-separated string | **Pattern whitelist**: keep only the listed pattern categories | `--patterns speed,jack,tech,stamina` |
| **`--exclude-patterns`** | comma-separated string | **Pattern blacklist**: exclude the listed pattern categories | `--exclude-patterns ln` or `--exclude-patterns ln,course` |
| **`--min-diff`** / `--min-expected` | float | **Minimum difficulty (inclusive)**: only maps with expected ND ≥ value | `--min-diff 10.5` |
| **`--max-diff`** / `--max-expected` | float | **Maximum difficulty (inclusive)**: only maps with expected ND ≤ value | `--max-diff 18.5` |
| **`--filter-name`** | string | **Name keyword filter**: only maps whose name contains the substring (case-insensitive) | `--filter-name "Darling"` |
| **`--filter-preset`** | preset name | **One-touch domain preset** | `--filter-preset daniel` (auto-excludes `ln`, restricts 10.5 ≤ ND ≤ 18.5) |

### 3.3 Automatic Course & LN Exclusion

The system enforces physical-consistency isolation:

- Dimension 1 (accuracy): if the user does not explicitly exclude them, `course` and `ln` are kept, measuring overall performance.
- **Dimensions 2–5 (perturbation tests)**: regardless of user filters, the system **automatically and mandatorily excludes `/samples/4k/course` and `/samples/4k/ln`** when sampling perturbation variants. Only rice-only single-map patterns (`jack`, `speed`, `stamina`, `tech`) are used for rate scaling, Gaussian jitter and short-LN injection.

### 3.4 Common Debugging Scenarios

#### Scenario A: Debug Daniel within its native target domain (non-LN, high tiers)

```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --filter-preset daniel \
  --mode standard \
  --output-dir results_daniel_native
```

*Effect*: 547 maps selected; calibrated ND-RMSE drops from 1.2190 (full set) to 0.4363, and Spearman ρ rises from 0.9525 to 0.9702.

#### Scenario B: Exclude LN maps, keep all difficulties

```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --exclude-patterns ln \
  --mode standard
```

#### Scenario C: Test only tech-pattern (Tech) and jack (Jack) categories

```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --patterns tech,jack \
  --mode quick
```

#### Scenario D: Focus on high-tier maps (ND ≥ 14.0)

```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --min-diff 14.0 \
  --exclude-patterns ln
```

#### Scenario E: Single-map debugging by title

```bash
./cab_run.sh run \
  --algorithm daniel \
  --scale daniel \
  --filter-name "AIR" \
  --mode quick
```

---

## 4. Python API

The filtering and evaluation interfaces can also be called directly from Python scripts or automated test suites:

```python
from cab.adapter.daniel import DanielAdapter
from cab.runner.config import BenchConfig
from cab.runner.suite import BenchmarkSuite
from cab.parser.bench_loader import BenchmarkDataset

# 1. Instantiate the adapter for the algorithm under test
adapter = DanielAdapter(scale_type="daniel")

# 2. Build an evaluation config with filters
config = BenchConfig.create(
    mode="standard",
    filter_preset="daniel",      # preset: exclude ln, ND in [10.5, 18.5]
    # or custom filters:
    # exclude_patterns=["ln", "course"],
    # min_expected=10.5,
    # max_expected=18.5,
    # name_filter="Darling",
)

# 3. Run the benchmark
suite = BenchmarkSuite(adapter=adapter, config=config)
result = suite.run_all(verbose=True)

# 4. Read metrics and scores
print(f"Total CAB-Score: {result.score.total_score:.1f} / 100")
print(f"Calibrated ND-RMSE: {result.accuracy.global_rmse:.4f}")
print(f"Spearman Rho: {result.accuracy.spearman_rho:.4f}")
```

---

## 5. Algorithm Integration Protocol

> [!IMPORTANT]
> **Algorithms adapt to the benchmark; the benchmark does not adapt to algorithms.**
> CAB-VA is an independent, authoritative evaluation benchmark: **it only provides maps to algorithms and accepts plain-text difficulty values back**.
> The core is fully decoupled from concrete algorithm implementations (black-box decoupling): no glue code is needed inside the benchmark for any specific algorithm. Any future algorithm that follows the standard I/O protocol can join the leaderboard directly.
> *(Note: the built-in Daniel / Mixed / MinaCalc / Sunny / Dan-Overlay bridges are conveniences for local comparison during development; they are not required couplings of the benchmark core.)*

### 5.1 Plain-Text CLI & Text Protocol

Any algorithm meeting the following plain-text protocol can run the full benchmark with zero code intrusion via `--cmd`:

1. **Input**: the benchmark passes the absolute path of the map to evaluate (via the `{input}` placeholder, e.g. `/tmp/test_map.osu`);
2. **Output**: the algorithm prints **a single-line plain-text float** (or JSON containing `{"difficulty": float}`) to stdout:
   ```text
   14.852000
   ```
3. **One-liner run**:
   ```bash
   # Test external algorithms in any language (C++ / Rust / C# / Python / Go)
   ./cab_run.sh run --cmd "./my_algorithm --file {input}" --mode quick
   ```

### 5.2 Pure Black-Box LN Capability Probing (HO Probe)

Because the benchmark treats algorithms strictly as black boxes (maps in, a number out), it **never relies on authors' claims or internal code inspection**. LN capability is determined purely by dynamic behavior probing:

1. **Original input**: feed real LN maps (`/samples/4k/ln/`) to the algorithm → $D_{\text{orig}}$;
2. **HO-stripped input**: generate control maps with the standard HO algorithm (all hold tails stripped, converted to pure rice taps) → $D_{\text{ho}}$;
3. **Black-box verdict**:
   $$\Delta_{\text{HO}} = \frac{|D_{\text{orig}} - D_{\text{ho}}|}{\max(0.01, D_{\text{orig}})}$$
   - If $\Delta_{\text{HO}} < 2.0\%$ (or the two are exactly equal): the algorithm is objectively proven to "**brute-force all LNs as rice taps, with no real release modeling**".
   - **Penalty**: the algorithm is treated as defective at anti-short-LN testing and **scored 0.0 (Grade: F)**.
   - Additionally, if the tester actively excludes the LN folder (e.g. `--exclude-patterns ln`), the run does not cover LN capability either, **and the anti-short-LN score is likewise forced to 0.0**.

### 5.3 Difficulty Scale Type (`--scale`)

> **Terminology**: **ND** is short for **Numeric Difficulty** — the continuous difficulty scale that expresses Greek-letter dan tiers (Alpha 11.0, Beta 12.0, …, Theta 18.0) as numeric values. It is the default ground-truth scale of this benchmark.

- **`ng`**: native ND-scale output (e.g. 0.0 ~ 20.0+);
- **`daniel`**: Daniel scale (linear alignment $daniel - 0.5 = ND$);
- **`non-ng` / `auto`**: any custom scale (pure star rating SR, MSD, BMS star level, etc.). The system maps via monotone isotonic splines (PAVA) and faithfully records macro-tier inversions.

### 5.4 Multi-Tier Evaluation (`--tier`)

CAB-VA natively supports multi-tier sliced evaluation to accommodate different algorithms' design domains:

| Tier flag (`--tier`) | Name | Difficulty range | Sample size (full) | Focus & positioning |
| :--- | :--- | :---: | :---: | :--- |
| **`multi`** *(default)* | **Automatic 4-tier joint run** | all | 832 maps | **Default**: single-run, zero-redundancy output of all 4 tier reports and comparison charts |
| **`all`** | **All Range** | ≥ -2.5 | 832 maps | Overall capability and cross-tier generality (full 6 dimensions) |
| **`number`** | **Number Dan** | [-2.5, 10.5) | 196 maps | Intro 1~3 through 10th Dan; low-speed perception and density continuity (static 4 dims) |
| **`greek`** | **Greek Dan** | [10.5, 15.5] | 553 maps | Alpha–Epsilon; the core competitive/exam tier (static 4 dims) |
| **`inhuman`** | **Inhuman Dan** | > 15.5 | 83 maps | Zeta through Theta/Kappa (18+); extrapolation stability at physical limits (static 4 dims) |

#### 1. Sub-tier dimension pruning & re-weighting

- **Dynamic rate dimensions do not participate in sub-tiers**: AIN (dim 2) and CT (dim 4) are dynamic clock-scaling perturbations (1.01x ~ 2.00x). Rate-increased maps necessarily cross fixed static tier boundaries, so they are **evaluated only in All Range**, shown as `-` in Number/Greek/Inhuman.
- **Static 4 dimensions renormalized to 100**: the four dimensions keep their original All-Range ratios (ACC:ASLN:Speed:AJ = 40:15:5:10), scaled proportionally:
  - **Accuracy (ACC)**: **57.14%** (40/70)
  - **Anti-Short-LN (ASLN)**: **21.43%** (15/70)
  - **Computation Speed (Speed)**: **7.14%** (5/70)
  - **Anti-Jitter (AJ)**: **14.29%** (10/70)
  - Total: $57.14\% + 21.43\% + 7.14\% + 14.29\% = 100\%$.

#### 2. Tier slicing vs. algorithm domain restriction

- **Tier**: the benchmark's objective partition of the whole VSRG library.
- **Algorithm domain restriction**: if an algorithm declares a compute domain (e.g. `--min-diff 12.5`):
  - **All Range (`all`)**: evaluate all 6 dimensions on valid maps ($[12.5, +\infty)$); dynamic rate sampling also restricted to ≥ 12.5.
  - **Number Dan (`number`)**: its range $[-2.5, 10.5)$ has an empty intersection with the domain → **`N/A (out of algorithm domain)`**.
  - **Greek Dan (`greek`)**: adapts to the domain, evaluating only $[12.5, 15.5]$ with static 4 dimensions.
  - **Inhuman Dan (`inhuman`)**: evaluates only maps > 15.5, static 4 dimensions.

#### Multi-tier run examples

```bash
# Default: automatic joint run of the 4 tiers (recommended):
./cab_run.sh run --algorithm mixed --mode quick

# Test a specialized algorithm in the core Greek range only:
./cab_run.sh run --algorithm daniel --tier greek --mode standard

# Declare a domain lower bound (e.g. 12.5+); Number Dan auto-marked N/A:
./cab_run.sh run --algorithm daniel --min-diff 12.5 --mode quick
```

### 5.5 Fault-Tolerant Beatmap Error Isolation

Experimental algorithms may crash on extreme map formats (abnormal LN overlaps, negative timestamps, non-standard BPM, etc.).

To keep benchmarks complete and debugging efficient, CAB-VA provides end-to-end fault isolation:

1. **Automatic skip & exclusion**: a crashing, erroring or timed-out map is removed from the sample pool without aborting the run;
2. **Console alerts**: excluded maps are listed on the console with filename, expected difficulty and an error summary;
3. **Report traceability**: the generated Markdown reports (`CAB_BENCHMARK_REPORT.md` and `CAB_MULTI_TIER_SUMMARY.md`) and JSON exports include a dedicated "**Excluded Failed Beatmaps**" section, so authors can locate and fix bugs precisely.

---

## 6. Outputs & Artifacts

After each run, the output directory (default `results/`) contains complete data and charts:

### 6.1 Multi-Tier Artifacts

- **`CAB_MULTI_TIER_SUMMARY.md`**: cross-tier comparison report with scorecards, radar charts, per-dimension breakdowns, and domain-fitness diagnosis;
- **`cab_multi_tier_summary.json`**: machine-readable 4-tier data;
- **`cab_multi_tier_comparison.png`**: standardized 6-dimension comparison across tiers;
- **`CAB_BENCHMARK_REPORT.md`**: detailed All-Range (master baseline) report;
- **`cab_benchmark_result.json`**: detailed All-Range JSON result.

### 6.2 Per-Tier Subdirectories

Each tier also gets its own subdirectory with independent reports and diagnostic charts:

- **`all/`**: All-Range report, radar chart, calibration curve and inversion profiles;
- **`number_dan/`**: Number Dan report and metrics;
- **`greek_dan/`**: Greek Dan report and metrics;
- **`inhuman_dan/`**: Inhuman Dan report and metrics.

### 6.3 Per-Map Predictions CSV

On top of the aggregate metrics, every run **by default** also writes a per-beatmap breakdown — i.e. what score the algorithm actually assigned to each individual map:

```bash
python -m cab.cli run -a mixed -o results_mixed
# writes results_mixed/predictions.csv automatically
```

Use `--predictions-file` to rename or relocate it, and `--no-export-predictions` to turn it off entirely:

```bash
python -m cab.cli run -a mixed -o results_mixed --predictions-file mixed_maps.csv
python -m cab.cli run -a mixed -o results_mixed --no-export-predictions
```

Artifact locations:
- **Single-tier mode**: `predictions.csv` in the output directory
- **Multi-tier mode (`--tier multi`, the default)**: `predictions.csv` at the output root (merged All-Range table), plus one file inside each tier folder — `all/predictions.csv`, `number_dan/predictions.csv`, `greek_dan/predictions.csv`, `inhuman_dan/predictions.csv`

Columns (12 total; the first 9 are the full-dataset readings, the last 3 are the `--declare-scope` in-scope reading):

| Column | Meaning |
| :--- | :--- |
| `path` | Beatmap path relative to `samples/4k/`, using the **same convention** as the `path` column of `uuzs_bench.csv` |
| `name` | Beatmap name |
| `pattern` | Broad pattern class (tech / jack / speed / stamina / ln / course / unsigned) |
| `subPattern` | Fine-grained pattern label |
| `expected` | Ground-truth Numeric Difficulty (ND) difficulty |
| `got` | The algorithm's **raw native reading** (pre-calibration) — the number the algorithm itself produced, not yet converted onto the ND ruler |
| `numericdifficulty` | That reading **converted onto the ND ruler** (post-calibration; same basis as the reported RMSE). For native-ND algorithms (`scale_type=ng`) this column equals `got` |
| `delta` | `numericdifficulty - expected`; positive means the algorithm overrates the map |
| `deltaAbs` | `abs(delta)` |
| `ndinscale` | In-scope ND value: the reading **re-calibrated with anchors fitted on the declared scope alone**; `NaN` outside the scope |
| `deltainscale` | `ndinscale - expected`; `NaN` outside the scope |
| `deltaAbsinscale` | `abs(deltainscale)`; `NaN` outside the scope |

> **Tip**: `path` / `name` / `pattern` / `subPattern` share their names with the ground-truth file `samples/4k/uuzs_bench.csv`, so the two can be joined directly on `path` with no renaming. Recomputed RMSE from the `delta` column matches the Ground-Truth Accuracy figure in the report exactly, which makes third-party verification straightforward.
>
> **Note**: `delta` is measured against `numericdifficulty` (post-calibration) rather than `got` (raw), because the Accuracy dimension scores readings after they have been unified onto a common ruler. For algorithms on a non-ND scale (e.g. the Daniel family), `got` and `numericdifficulty` will differ noticeably — that is expected.
>
> **Declared-scope columns**: with no `--declare-scope`, the last three columns mirror `numericdifficulty` / `delta` / `deltaAbs` exactly. With a declaration active, in-scope beatmaps carry the scope-only re-calibrated values (differing from the global readings is normal) and out-of-scope beatmaps carry `NaN`. Full details in §7.3.

---

## 7. Filter Presets & Domain Profiling

### 7.1 Filter Presets

Beyond specifying filter flags one by one, the system offers **one-touch domain presets**. Presets live in a single maintainable constant table (`FILTER_PRESETS` in `cab/runner/config.py`), which is the one source of truth: the CLI choices are derived from the table's keys automatically. Adding a preset means appending one entry to the table — no judgment logic needs to change.

Built-in presets:

| Preset | Expansion | Semantics |
| :--- | :--- | :--- |
| `daniel` | `exclude_patterns=["ln"]` + `10.5 ≤ ND ≤ 18.5` | Daniel's native target domain: non-LN, high-tier range |

> **Note**: Explicit flags (`--exclude-patterns` / `--min-diff` / `--max-diff`, etc.) **take precedence over the preset**, so you can further narrow the scope on top of a preset.

There is also a scale parameter `--scale daniel` (see Section 5.3) that is **independent of presets**. Its semantics are "map the algorithm's native output directly to the ND scale by subtracting 0.5". It does not interact with domain-filter presets, and the two can be freely combined.

### 7.2 Domain Fitness Profile

In standard mode the accuracy dimension is always computed over the **full dataset** and is never narrowed by filters. So that reports can still state *which domains an algorithm handles well and which it does not*, every report now carries a **programmatically derived** domain-fitness profile. The conclusion is computed entirely from data — no human-written prose — so it stays maintainable over time.

**Method** (cohort-internal baseline):

1. Take the per-pattern RMSE of the algorithm and compute their **median** as the algorithm's internal baseline;
2. For each pattern compute `ratio = pattern RMSE / baseline`;
3. Assign a verdict per the table below.

| `ratio` range | Verdict | Meaning |
| :---: | :---: | :--- |
| `ratio ≤ 0.80` | **Strength** | Error materially below the algorithm's own average |
| `0.80 < ratio ≤ 1.20` | **Adequate** | Error on par with the algorithm's overall level |
| `ratio > 1.20` | **Weakness** | Error materially above the algorithm's own average |
| sample count `< 20` | **Under-sampled** | Not judged, to prevent small-sample flukes |

The profile appears in two artifacts:

- **Markdown**: below the "Pattern-Stratified Breakdown" table in `CAB_BENCHMARK_REPORT.md` and `CAB_MULTI_TIER_SUMMARY.md`, with a full verdict table plus strength/weakness callouts;
- **JSON**: `dimension_1_accuracy.stratified_counts` (per-pattern sample counts) and `dimension_1_accuracy.domain_profile` (structured profile with `strengths` / `weaknesses` / `adequate` / `insufficient` / `verdicts`).

> **Tunable**: the thresholds live in `cab/report/profiling.py` (`_DEFAULT_MIN_SAMPLES`, `_DEFAULT_STRENGTH_RATIO`, `_DEFAULT_WEAKNESS_RATIO`).

### 7.3 Declared Difficulty Scope (`--declare-scope`)

Some algorithms are deliberately built for a slice of the difficulty space: pure-Rice algorithms have no LN model, and several dan estimators only cover the higher tiers. Scored on the full dataset, such an algorithm is penalised for beatmaps it never attempted; scored only on its own slice, its number is no longer comparable with general-purpose algorithms.

CAB-VA resolves this **without any penalty**:

- The algorithm (or its evaluator) **declares** what it does not attempt via `--declare-scope`. The declaration is explicit CLI input — the benchmark **never** infers or adapts to an algorithm's behaviour on its own;
- **The headline score and every one of the six dimensions are still computed over the full dataset.** A declaration never changes them, so algorithms remain directly comparable and over-declaring buys nothing;
- On top of that, the report adds a **secondary in-scope reading**: accuracy (ND-RMSE / MAE / Spearman ρ / WTD) computed over the kept beatmaps only, **with its own freshly fitted calibration anchors**, so a specialised algorithm can show how strong it really is inside the slice it targets;
- It also estimates the **real-world usage given up** by the declaration (from the public analyser's 90-day usage distribution; reporting only, never feeds a score).

Both readings are reported side by side; the reader is left to judge. Nothing is hidden behind a weighting constant.

Syntax (repeatable; multiple declarations combine with **OR** semantics; the comparison names the side that is dropped, the boundary value itself is always kept, and `<` and `<=` behave identically, as do `>` and `>=`):

| Term | Meaning | Example |
| :--- | :--- | :--- |
| `pattern:<name>[,<name>...]` | pattern classes not attempted (`ln` / `course` / `jack` / `speed` / `stamina` / `tech` / `unsigned`) | `pattern:ln` or `pattern:ln,tech` |
| `nd<X` / `nd<=X` | drop everything below X | `nd<10.5` (attempt only ND ≥ 10.5) |
| `nd>X` / `nd>=X` | drop everything above X | `nd>=15.5` (attempt only ND ≤ 15.5) |

(`ng` and `difficulty` are accepted aliases of `nd`.)

Example:

```bash
# A Daniel-style declaration: skip the LN family and only attempt ND >= 10.5
./cab_run.sh run --algorithm daniel --mode standard \
  --declare-scope "pattern:ln nd<10.5" \
  --output-dir results_daniel_declared
```

Where it appears:

- **Console**: an `In-scope (...)` line while the run proceeds (RMSE / kept charts / usage given up);
- **Markdown report**: the "Declared-Scope Reading" subsection in §2.1 (only when a scope is active);
- **JSON**: the top-level `declared_scope` field (with `in_scope_accuracy`; `null` when nothing is declared);
- **Per-map CSV**: the `ndinscale` / `deltainscale` / `deltaAbsinscale` columns (`NaN` outside the scope; see §6.3).

> **Difference from dataset filtering**: filter flags (`--exclude-patterns` / `--filter-preset` / ...) genuinely **narrow the evaluation** (scores are computed on the filtered set); `--declare-scope` **never changes any score** — it only adds the in-scope reading and the usage disclosure. Use filters to reproduce a domain-restricted run; use declarations to keep the full-dataset headline and still explain in-domain strength.
