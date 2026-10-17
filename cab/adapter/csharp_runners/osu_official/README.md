# osu_official runner

A small .NET console app that computes the **official osu!lazer mania star
rating** for a `.osu` beatmap. It references `osu.Game` (and
`osu.Game.Rulesets.Mania`) as project dependencies, built from a local
checkout of [ppy/osu](https://github.com/ppy/osu) `master`, so the numbers are
produced by the same code path osu!lazer itself uses for its official mania
difficulty. No mods are applied.

## Prerequisites

- .NET 10 SDK (>= 10.0.100). User-level install:
  `curl -sSL https://dot.net/v1/dotnet-install.sh | bash -s -- --channel 10.0`
- A local clone of ppy/osu at `test_algorithms/osu` (shallow is fine):
  `git clone --depth 1 https://github.com/ppy/osu test_algorithms/osu`

## Build

```bash
# from this directory
dotnet build -c Release          # or ~/.dotnet/dotnet build -c Release
```

Output: `bin/Release/net10.0/osu_official.dll`.

## Usage

Use the bridge script (handles toolchain discovery):

```bash
# one-shot
python3 cab/adapter/osu_official_cli.py path/to/map.osu

# persistent server (newline-delimited JSON protocol)
python3 cab/adapter/osu_official_cli.py --server
```

## Validation

Cross-checked against ppy/osu-tools' `PerformanceCalculator difficulty
--ruleset:3` command (built from the same local osu checkout): reference maps
produce bit-identical star ratings (diff = 0.0e+00, e.g. `3.9998502314622892`
for a Stage V jack chart and `8.475165362166306` for a x1.35 tech chart).

## Provenance

The exact revisions in use are whatever the local checkouts hold:

```bash
git -C test_algorithms/osu rev-parse HEAD          # ppy/osu
git -C test_algorithms/osu-tools rev-parse HEAD    # ppy/osu-tools (used for cross-checks)
```
