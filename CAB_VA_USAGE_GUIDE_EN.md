# CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm — User & Debugging Guide

**中文版**: [CAB_VA_USAGE_GUIDE.md](./CAB_VA_USAGE_GUIDE.md)

---

## 1. System Overview & Evaluation System

CAB-VA is an industrial-grade, multi-dimensional quantitative benchmark framework for VSRG difficulty algorithms (osu!mania, Malody, BMS, Etterna, etc.). It covers six core dimensions (each scored out of 100; total 100):

1. **Dimension 1: Ground-Truth Accuracy & Calibration (score backbone)**
   - Checks rating accuracy against the benchmark map library: calibrated ND-RMSE, global MAE, Spearman rank correlation ρ, and within-tier dispersion (WTD).
   - **RMSE metric**: the RMSE sub-score of the accuracy dimension uses **two-level weighting** — fixed pattern weights (jack/speed/stamina/tech 0.20 each, ln 0.15, course/unsigned 0.05 each) split inside each pattern across **sub-patterns** (binomial `pattern-subpattern` ids; classified buckets count 3, `Unsigned` counts 1, re-normalised within the pattern) rather than a sample-count-weighted global RMSE, so that large-sample buckets cannot dominate the total (see "ACC Metric: Sub-Pattern Weighting" in the README).
   - **Sample rule**: includes marathon courses and dan-course sets (`/samples/4k/course`) plus regular single maps by default.
2. **Dimension 2: Anti-Rate-Inversion / Monotonicity (discount β 15%)**
   - Verifies strict non-decreasing behavior of difficulty output as clock rate increases (1.0x ~ 2.0x).
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**; physical clock transforms are applied only to rice-only single maps.
3. **Dimension 3: Anti-Short LN Release-Inflation Robustness (discount β 15%)**
   - Converts regular rice notes into 20ms~100ms short LNs (release LNs) to test whether an algorithm inflates spuriously due to mapper release timing.
   - **Two-sided tolerance band**: injecting short LNs adds real objects to the chart, so a faithful model *must* show a measurable difficulty increase. Scoring is therefore not maximised at "zero inflation" but at a realistic release overhead:
     $$\text{ASLN}_{\text{band}} = \frac{100}{1+\left(\frac{\left|\text{ITR} - 7\right|}{8}\right)^{1.8}}$$
     where ITR is the relative inflation percentage. **The further from 7%, the lower the score — above and below are penalised equally**: over-inflation (release-tail artifacts) and zero-response (blind to LNs) are both distortions.
   - **Grades** by distance to 7%: ≤2 → S, ≤5 → A, ≤10 → B, ≤20 → C, otherwise F.
   - **Continuous Hold-Off Capability Gate**:
     Relying solely on the two-sided band would allow algorithms that strip LNs or ignore release timing entirely to score a mediocre ~56 points. We introduce a continuous capability gate based on the difficulty delta $\Delta_{\text{HO}}$ when 10 full-LN charts are converted to rice taps:
     $$S_{\text{ASLN}} = \text{ASLN}_{\text{band}} \times G_{\text{LN}}(\Delta_{\text{HO}}), \quad G_{\text{LN}} = \frac{r^{2.5}}{1 + r^{2.5}}, \quad r = \frac{\Delta_{\text{HO}}}{5.0\%}$$
     - If an algorithm is completely blind to LNs or brute-force strips them ($\Delta_{\text{HO}} = 0.00\%$, such as Daniel, MinaCalc, Interlude), the gate strictly evaluates to $0.0$, driving its ASLN score to **0.00 pts (Grade: F / LN-Blind)**;
     - If the tester actively excludes the `ln` folder, there are no measurable readings, so the score is likewise forced to 0.0 pts.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.
4. **Dimension 4: Local Continuity & Cliffing (discount β 10%)**
   - Computes the empirical local Lipschitz constant along rate curves, detecting and penalizing cliff jumps caused by hard step thresholds.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.
5. **Dimension 5: Computation Speed & Cost (discount β 5%)**
   - Evaluates compute cost in ms/s (compute milliseconds per music second), then converts it to a **same-session ratio against the MinaCalc baseline** (Hill curve: 50x faster than the baseline = 85 points, 2x slower = 60 points; 100 = instant completion, never reachable).
   - **Sample rule**: the standard benchmark map set.
6. **Dimension 6: Anti-Jitter Perturbation Robustness (discount β 5%)**
   - **OD-Adaptive Microscopic Timing Jitter**: timing perturbation bounds adapt dynamically to the chart's OD hit window ($W_{300}(\text{OD}) = 64.0 - 3.0 \times \text{OD}$ ms). The per-note jitter cap is set to $\text{max\_ms} = 0.25 \times W_{300}(\text{OD})$ (OD7~10 corresponds to $9.1\sim 10.8\text{ ms}$), with $\sigma = 0.55\times\text{max\_ms}$ and symmetric midpoint collision constraints between adjacent notes. The micro-jitter falls strictly within the player's 300 judgement window, leaving subjective playfeel completely unchanged; a robust algorithm should demonstrate high micro-timing stability (low MAPE).
   - **Intra-Window Same-Density Arrangement Distortion Probe & Gate**:
     Algorithms that compute density over a 400ms sliding window while completely ignoring intra-window note timing/patterns (e.g. osu!lazer and legacy osu!mania) previously scored an inflated 92+ points on naive jitter. We introduce Probe A: pulling consecutive notes 58% closer within 400ms windows to create 400+ BPM bursts while keeping window note counts and lane assignments 100% invariant.
     A continuous Hill gate is applied based on arrangement sensitivity $\Delta_{\text{arrange}}$:
     $$S_{\text{AJ}} = S_{\text{base}}(\text{MAPE}) \times G_{\text{arrange}}(\Delta_{\text{arrange}}), \quad G_{\text{arrange}} = \frac{r^{2.5}}{1 + r^{2.5}}, \quad r = \frac{\Delta_{\text{arrange}}}{3.5\%}$$
     Algorithms blind to note patterns (such as osu! official algorithms, $\Delta_{\text{arrange}} \approx 2.1\%$) receive a steep discount (scores dropping to ~20 pts), honestly reflecting their disregard for intra-window patterns.
   - **Sample rule**: **strictly excludes `/samples/4k/course` and `/samples/4k/ln`**.

**Composite rule: ACC backbone x multiplicative discounts**

$$\text{Total} = \text{ACC}\times\prod_i\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$

- **β is not a weight**: it means "the fraction of the total lost if this dimension scores 0". A perfect score gives 1.0 — **no discount, and no bonus** — so saturated dimensions (6 of 10 algorithms hit exactly 100 on Local Continuity) no longer hand everyone a free constant.
- **Non-compensatory**: a strong dimension cannot buy back a weak one. Under the old weighted sum an algorithm could rank 8th on accuracy yet 3rd on total; here accuracy is the backbone, so weaknesses can only subtract.
- **All-Range β**: AIN **15%** = ASLN **15%** > CT **10%** > Speed **5%** = AJ **5%** (derived from `BETA_BASE` 0.30/0.30/0.20/0.10/0.10 times `BETA_SCALE = 0.5`).
- **Sub-tiers**: the dynamic rate dimensions (AIN / CT) and Computation Speed (SP) are not evaluated there (AIN/CT because rate scaling necessarily crosses static tier boundaries; SP because it is a global, once-measured metric over the full probe), so their discounts simply drop out of the product — no renormalisation needed. That is a structural simplification over the old weight system.

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
| **`--filter-preset`** | preset name | ⚠️ **Debug use only**: a one-touch domain preset that *physically shrinks the dataset*, so its scores are not comparable with the published leaderboard (use `--declare-scope` for a public scope claim; see §7.3) | `--filter-preset daniel` (auto-excludes `ln`, restricts 10.5 ≤ ND ≤ 18.5) |

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
   - **Outcome**: the continuous capability gate drives its score to zero:
     $$S_{\text{ASLN}} = \text{ASLN}_{\text{band}} \times G_{\text{LN}}(\Delta_{\text{HO}}), \quad G_{\text{LN}} = \frac{r^{2.5}}{1 + r^{2.5}}, \quad r = \frac{\Delta_{\text{HO}}}{5.0\%}$$
     If $\Delta_{\text{HO}} = 0.00\%$ (e.g. Daniel, MinaCalc, Interlude), the gate strictly evaluates to $0.0$, yielding an ASLN score of **`0.00 pts (Grade: F / LN-Blind)`**. This eliminates the loophole where LN-blind algorithms received an unearned ~56 points, fundamentally protecting algorithms with authentic LN modeling.
   - Additionally, if the tester actively excludes the LN folder (e.g. `--exclude-patterns ln`), the run has **no measurable reading at all**, so the anti-short-LN score is still forced to 0.0 (Grade: F).

### 5.3 Difficulty Scale Type (`--scale`)

> **Terminology**: **ND** is short for **Numeric Difficulty** — the continuous difficulty scale that expresses Greek-letter dan tiers (Alpha 11.0, Beta 12.0, …, Theta 18.0) as numeric values. It is the default ground-truth scale of this benchmark.

- **`nd`**: native ND-scale output (e.g. 0.0 ~ 20.0+);
- **`daniel`**: Daniel scale (linear alignment $daniel - 0.5 = ND$);
- **`non-nd` / `auto`**: any custom scale (pure star rating SR, MSD, BMS star level, etc.). The system maps via monotone isotonic splines (PAVA) and faithfully records macro-tier inversions.

### 5.4 Multi-Tier Evaluation (`--tier`)

CAB-VA natively supports multi-tier sliced evaluation to accommodate different algorithms' design domains:

| Tier flag (`--tier`) | Name | Difficulty range | Sample size (full) | Focus & positioning |
| :--- | :--- | :---: | :---: | :--- |
| **`multi`** *(default)* | **Automatic 4-tier joint run** | all | 856 maps | **Default**: single-run, zero-redundancy output of all 4 tier reports and comparison charts |
| **`all`** | **All Range** | ≥ -2.5 | 856 maps | Overall capability and cross-tier generality (full 6 dimensions) |
| **`number`** | **Number Dan** | [-2.5, 10.5) | 207 maps | Intro 1~3 through 10th Dan; low-speed perception and density continuity (static 3 dims) |
| **`greek`** | **Greek Dan** | [10.5, 15.5] | 563 maps | Alpha–Epsilon; the core competitive/exam tier (static 3 dims) |
| **`inhuman`** | **Inhuman Dan** | > 15.5 | 86 maps | Zeta through Theta/Kappa (18+); extrapolation stability at physical limits (static 3 dims) |

#### 1. Sub-tier dimension pruning & multiplicative discount rule

- **Dynamic rate dimensions and Speed do not participate in sub-tiers**: AIN (dim 2) and CT (dim 4) are dynamic clock-scaling perturbations (1.0x ~ 2.00x). Rate-increased maps necessarily cross fixed static tier boundaries, so they are **evaluated only in All Range**, shown as `-` in Number/Greek/Inhuman. Likewise, **Computation Speed (SP) is an algorithm-level global metric** (one measurement over the 33-chart probe) and is independent of tier slicing, so it is not scored in sub-tiers either.
- **Sub-tier composite under multiplicative discounts**:
  $$\text{Total}_{\text{subtier}} = \text{ACC}\times\prod_{i\in\{\text{ASLN, AJ}\}}\left[1-\beta_i\left(1-\frac{s_i}{100}\right)\right]$$
  Per-dimension discount strengths $\beta_i$ stay identical to All Range (ASLN 15%, AJ 5%). The unevaluated AIN, CT and SP simply drop out of the product (discount = 1.0) with **no weight renormalisation needed**, preserving ACC backbone integrity and penalty consistency.

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

### 5.6 SP Provenance and `--sp-source`

**Computation Speed (SP) is the only dimension measured as raw wall-clock time**, so its reading carries two layers of noise that have nothing to do with the algorithm:

1. **Machine variance** — single-core performance differs between CPUs, so ms/s is not comparable across machines;
2. **Platform-layer overhead** — if an algorithm ships a Windows-only binary, any non-Windows platform must run it through a compatibility layer, and that layer's process startup is pure added cost that does not exist on the target platform. It is counted into SP at 100%.

Dan-Overlay is the textbook case: `tools/bin/` only ships `msd.exe` (PE32+), so Linux must call it through Wine. Measured on one machine (i7-14650HX):

| Measurement | Result |
| :--- | ---: |
| Single `wine msd.exe` launch | 4970 ms |
| Child CPU time inside that | 55 ms (1.1%; the rest is spent sleeping) |
| A 2-note map through the same path | 4976 ms (= the fixed overhead alone) |
| Effect on Dan-Overlay SP | 58.45 (wine ≈0.17 s/call) → **28.93** (wine ≈5 s/call) |
| After automatic subtraction | **68.3 – 68.6** (two reruns, 0.28 apart) |

In other words, SP is then measuring "how fast Wine starts", not how fast the algorithm is. CAB-VA handles this with three layers (same-session MinaCalc baseline, compatibility-overhead subtraction, cross-environment archives).

**(1) Automatic subtraction of compatibility-layer overhead (on by default)**

Before the speed probe runs, the same persistent server measures the fixed cost of one invocation using a two-note map, and that cost is subtracted from every sample. SP then reflects the algorithm's own overhead, equivalent to running the binary natively on the target platform.

Implementation notes:

- the baseline must be measured in the **same worker and the same persistent server** as the real samples. An earlier version probed it from a separate process and reported ~80 ms too much (5087 ms vs 4987 ms), over-subtracting from every chart and making the algorithm look faster than it is;
- nothing is subtracted unless the binary actually goes through a compatibility layer; on a native run the probe returns 0;
- a sample shorter than a quarter of the baseline counts as "never went through the layer" (cache hit or native) and is left untouched, so it can never be driven negative;
- the subtracted value is written to the `platform_overhead_ms` field of the JSON for auditing.

**(2) SP provenance via `--sp-source` (cross-environment reuse)**

To remove environment variance entirely — for example when an algorithm only runs natively on Windows while the benchmark runs on Linux — measure SP once in the target environment, archive it, then reuse it while the other five dimensions keep running locally.

```bash
# Step 1: measure SP alone in the target environment
#         (for Dan-Overlay, prefer a native Windows run)
./cab_run.sh run -a dan-overlay -m standard -o sp_sources/dan-overlay_windows --sp-only

# Step 2: every later run reuses that SP; the other five dimensions are measured live
./cab_run.sh run -a dan-overlay -m standard -o results_dan-overlay_standard \
    --sp-source sp_sources/dan-overlay_windows
```

Notes:
- `--sp-source` **overrides SP only** — ACC / AIN / ASLN / CT / AJ are unaffected;
- the `cab_benchmark_result.json` written by `--sp-only` contains an `environment` block (CPU model, core count, memory, OS, Python version, `wine_executable`) so the origin of the reading stays auditable;
- `cab_run.sh` is a bash script and does not run on Windows; use `pip install -e .` followed by `python -m cab.cli run ...`;
- re-measure and overwrite the archive whenever the algorithm itself changes.

**(3) Same-session MinaCalc baseline (on by default)**

After the 33-chart speed probe for the algorithm under test, the benchmark runs the built-in MinaCalc over the **same probe, on the same machine, moments later**, giving the baseline speed `v_ref`. SP then scores the ratio instead of absolute ms/s:

$$\rho = \frac{v_{\text{algo}}}{v_{\text{ref}}}, \qquad \text{SP} = \frac{100}{1 + (\rho / 386.25)^{0.2886}}$$

On the reference machine (MinaCalc = 0.0211 ms/s) this Hill curve passes exactly through **0.02 ms/s -> 85 points** and **2 ms/s -> 60 points**; 100 is an asymptote (instant completion) that no real algorithm reaches. Because $\rho$ is a ratio, both the machine's absolute speed and the load state at benchmark time cancel out — the same algorithm reads the same on another machine, or on the same machine when busy vs idle (measured: absolute speed varied 3x while the ratio moved only 12-19%). The reading records `baseline_ratio` / `baseline_ms_per_s` / `baseline_algorithm` for audit; when the algorithm under test is MinaCalc itself its own reading is reused ($\rho = 1$).

---

## 6. Outputs & Artifacts

After each run, the output directory (default `results/`) contains complete data and charts:

### 6.1 Multi-Tier Artifacts

- **`CAB_MULTI_TIER_SUMMARY.md`**: cross-tier comparison report with scorecards, radar charts, per-dimension breakdowns, and domain-fitness diagnosis;
- **`cab_multi_tier_summary.json`**: machine-readable 4-tier data;
- **`cab_multi_tier_comparison.png`**: standardized 6-dimension comparison across tiers;
- **`CAB_BENCHMARK_REPORT.md`**: detailed All-Range (master baseline) report;
- **`cab_benchmark_result.json`**: detailed All-Range JSON result.

> Every Markdown report is emitted **in both languages**: `*_EN.md` carries the English version of the same numbers, and each tier subdirectory gets its own `CAB_BENCHMARK_REPORT_EN.md`.

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
| `numericdifficulty` | That reading **converted onto the ND ruler** (post-calibration; same basis as the reported RMSE). For native-ND algorithms (`scale_type=nd`) this column equals `got` |
| `delta` | `numericdifficulty - expected`; positive means the algorithm overrates the map |
| `deltaAbs` | `abs(delta)` |
| `ndinscope` | In-scope ND value: the reading **re-calibrated with anchors fitted on the declared scope alone**; `NaN` outside the scope |
| `deltainscope` | `ndinscope - expected`; `NaN` outside the scope |
| `deltaAbsinscope` | `abs(deltainscope)`; `NaN` outside the scope |

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

> **Algorithms may ship a default declaration**: when an algorithm states its own scope in public documentation, the built-in adapter carries a default declaration (applied when no `--declare-scope` is passed — a note is printed to stderr; an explicit flag overrides it). Only Dan-Overlay currently has one: `pattern:ln nd<10.5 nd>16.5`, derived from `ISOR.md` §0/§12 ("LN maps are unsupported and explicitly rejected") plus its self-reported VSRG band `Tiers 11.0–17.0`, with a 0.5 margin at both ends. **A default declaration only ever restates what the author already published; the benchmark never invents a scope for an algorithm.**

Syntax (repeatable; multiple declarations combine with **OR** semantics; the comparison names the side that is dropped, the boundary value itself is always kept, and `<` and `<=` behave identically, as do `>` and `>=`):

| Term | Meaning | Example |
| :--- | :--- | :--- |
| `pattern:<name>[,<name>...]` | pattern classes not attempted (`ln` / `course` / `jack` / `speed` / `stamina` / `tech` / `unsigned`) | `pattern:ln` or `pattern:ln,tech` |
| `nd<X` / `nd<=X` | drop everything below X | `nd<10.5` (attempt only ND ≥ 10.5) |
| `nd>X` / `nd>=X` | drop everything above X | `nd>=15.5` (attempt only ND ≤ 15.5) |

(`difficulty` is an accepted alias of `nd`.)

Example:

```bash
# A Daniel-style declaration: skip the LN pattern and only attempt ND >= 10.5
./cab_run.sh run --algorithm daniel --mode standard \
  --declare-scope "pattern:ln nd<10.5" \
  --output-dir results_daniel_declared
```

Where it appears:

- **Console**: an `In-scope (...)` line while the run proceeds (RMSE / ρ / **0–100 score** / in-scope inversion penalty / kept charts / usage given up);
- **Markdown report**: the "Declared-Scope Reading" subsection in §2.1 (only when a scope is active);
- **JSON**: the top-level `declared_scope` field (`in_scope_accuracy` carries `calibrated_rmse` / `global_mae` / `spearman_rho` / `mean_wtd` / `weighted_stratified_rmse` / `sample_count` / **`score`** / **`macro_severity`** / **`macro_penalty`**; the whole block is `null` when nothing is declared);
- **Per-map CSV**: the `ndinscope` / `deltainscope` / `deltaAbsinscope` columns (`NaN` outside the scope; see §6.3).

> **Definition of the in-scope 0–100 score (`score`)**: **exactly the same formula** as the ACC dimension — `60·ρ² + 40·exp(−1.2·max(0, wRMSE − 0.3))` — minus the structural-inversion penalty. Two things differ: (1) only in-scope charts are fitted and scored (anchors re-fitted independently); and (2) the inversion severity **S is recomputed from the in-scope anchors** and **never inherited from the full run** — once a declaration removes the charts responsible for a dip, the in-scope anchor sequence is usually close to monotonic, so reusing the full-run penalty would charge the same defect twice (measured on Dan-Overlay: full-run S = 0.160 → −14.40; in-scope S = 0.003 → −0.24). The column is therefore directly comparable with ACC without double-charging.

> **Difference from dataset filtering**: filter flags (`--exclude-patterns` / `--filter-preset` / ...) genuinely **narrow the evaluation** (scores are computed on the filtered set); `--declare-scope` **never changes any score** — it only adds the in-scope reading and the usage disclosure.
>
> **Always use `--declare-scope` for public reporting.** `--filter-preset` has been demoted to a **maintainer debugging tool** (the CLI prints a warning on stderr). It only answers internal questions such as "if I drop this algorithm into the domain it claims, what do its raw judgements look like?". Its scores must never be listed next to published results — even the six dimension scores then cover a different population.
