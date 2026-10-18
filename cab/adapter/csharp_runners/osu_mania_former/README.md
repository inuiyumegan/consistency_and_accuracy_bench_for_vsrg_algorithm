# osu_mania_former runner

Computes the **official osu!lazer mania star rating as produced immediately
before [ppy/osu#24109](https://github.com/ppy/osu/pull/24109)** — *"Adjust LN
value scaling to bring overrated LN maps more in line"*, merged 2023-08-17.

It references `osu.Game` and `osu.Game.Rulesets.Mania` from a local checkout of
[ppy/osu](https://github.com/ppy/osu) pinned to tag **`2023.815.0`**, the last
release cut before that merge. The binary therefore runs the *whole* 2023
difficulty pipeline, not today's pipeline with a few constants rolled back.

## What #24109 changed

The PR touched exactly one file, `osu.Game.Rulesets.Mania/Difficulty/Skills/Strain.cs`
(now split into `Difficulty/Evaluators/OverallStrainEvaluator.cs`):

| | before (2023.815.0) | after (#24109) |
| :--- | :---: | :---: |
| `release_threshold` | **24** | 30 |
| `holdAddition` logistic slope | **0.5** | 0.27 |
| `isOverlapping` | — | `&& startTime > startTimes[i]` |
| `holdFactor` | — | `&& startTime > startTimes[i]` |

Net effect: overrated LN charts are nerfed; rice charts are untouched.

## Prerequisites

- .NET 10 SDK (>= 10.0.100)
- A sparse checkout of ppy/osu at tag `2023.815.0`:

```bash
git clone --depth 1 --branch 2023.815.0 --filter=blob:none --sparse \
  https://github.com/ppy/osu test_algorithms/osu-former
cd test_algorithms/osu-former
git sparse-checkout set osu.Game osu.Game.Rulesets.Mania CodeAnalysis
```

> `CodeAnalysis/` must be included: the repo's `Directory.Build.props` points at
> `CodeAnalysis/BannedSymbols.txt` and `CodeAnalysis/osu.ruleset`, and the build
> fails without them.
>
> The checkout's own `global.json` pins SDK `6.0.100`, but that only affects
> builds *launched from inside that directory*. Build from this runner's
> directory and the SDK pin is ignored.

## Build

```bash
# from this directory
~/.dotnet/dotnet build -c Release -p:TreatWarningsAsErrors=false
```

`TreatWarningsAsErrors=false` is needed because the 2023 sources were written
against net6.0 and emit cross-target warnings under a net10 build.

Output: `bin/Release/net10.0/osu_mania_former.dll`.

## Usage

```bash
# one-shot
python3 cab/adapter/osu_mania_former_cli.py path/to/map.osu

# benchmark
./cab_run.sh run --algorithm osu-mania-former --mode standard \
  --output-dir results_osu-mania-former_standard
```

Algorithm keys accepted: `osu-mania-former`, `osu-former`, `mania-former`.

## Validation (CAB-VA dataset, 832-chart revision)

The measurements below were taken on the **832-chart revision** of the dataset,
inside the LN cross-check that motivated this variant. The benchmark suite has
since grown (currently 856 charts), so the slice counts here are historical
rather than current.

Measured against the current `osu_official` runner (ppy/osu `master`):

| Slice | n | mean Δ (former − current) | median | max | relative mean |
| :--- | ---: | ---: | ---: | ---: | ---: |
| `samples/4k/ln` | 102 | **+0.3180★** | +0.2730 | **+1.2009** (+24.6%) | **+5.29%** |
| `samples/4k/jack` (sample) | 20 | +0.0002★ | 0.0000 | +0.0010 | +0.00% |

98% of LN charts differ by more than 0.001★; 95% of sampled rice charts differ
by less. That matches the PR's own claim that only LN-heavy maps move.
