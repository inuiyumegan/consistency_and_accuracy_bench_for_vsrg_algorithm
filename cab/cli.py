"""CAB-VA CLI: Command-Line Interface for the VSRG Difficulty Algorithm Benchmark."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from cab.adapter.base import BaseAdapter
from cab.adapter.cli import CliAdapter
from cab.adapter.daniel import DanielAdapter
from cab.report.console import print_console_report
from cab.report.environment import collect_environment_info
from cab.report.json_export import export_multi_tier_to_json, export_to_json
from cab.report.metric_ledger import export_metric_ledger
from cab.report.markdown import generate_markdown_report, generate_multi_tier_summary_report
from cab.report.predictions_csv import export_predictions_csv
from cab.report.probe_ledger import export_probe_ledgers
from cab.report.visualization import generate_all_plots
from cab.runner.config import (
    DEFAULT_TIERS,
    FILTER_PRESETS,
    TIER_DEFINITIONS,
    BenchConfig,
)
from cab.runner.suite import BenchmarkSuite, MultiTierBenchmarkResult

#: Interpreter used to relaunch the bundled adapter shims (``dan_overlay_cli.py``
#: etc.).  Hard-coding ``python3`` works on POSIX but breaks on Windows, where
#: the interpreter is usually ``python.exe`` (or only reachable via the ``py``
#: launcher), so every adapter would fail to start its persistent server.
#: ``sys.executable`` is the interpreter already running this process; the
#: surrounding double quotes keep paths with spaces intact under both
#: ``/bin/sh`` and ``cmd.exe``.
_PY = f'"{sys.executable}"'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cab",
        description="CAB-VA: Consistency and Accuracy Bench for VSRG Algorithm",
    )
    subparsers = parser.add_subparsers(dest="subcommand", help="Available subcommands")

    # 'run' subcommand
    run_parser = subparsers.add_parser("run", help="Run benchmark suite on target algorithm")
    run_parser.add_argument(
        "--algorithm",
        "-a",
        default="daniel",
        help="Algorithm identifier or adapter type ('daniel', 'cli')",
    )
    run_parser.add_argument(
        "--daniel-path",
        default="test_algorithms/Daniel",
        help="Path to Daniel repository or src directory (if algorithm=daniel)",
    )
    run_parser.add_argument(
        "--cmd",
        default=None,
        help="CLI command template with {input} placeholder (if algorithm=cli)",
    )
    run_parser.add_argument(
        "--server-cmd",
        default=None,
        help="Persistent-server command for --cmd (no {input} placeholder); "
             "enables fast batch mode and keeps SP free of process start-up",
    )
    run_parser.add_argument(
        "--algo-name",
        default=None,
        help="Display name recorded in the reports (default: derived from "
             "--algorithm; required when running variants of the same engine "
             "through --cmd so their outputs stay distinguishable)",
    )
    run_parser.add_argument(
        "--mode",
        "-m",
        choices=["quick", "standard", "full"],
        default="standard",
        help="Benchmark preset: standard (default, 100%% full dataset across all dimensions), quick (fast sampling check for dev), full (100 rate steps)",
    )
    run_parser.add_argument(
        "--scale",
        choices=["auto", "nd", "non-nd", "daniel"],
        default="auto",
        help="Algorithm difficulty scale type (default: auto)",
    )
    run_parser.add_argument(
        "--tier",
        choices=["multi", "all", "number", "greek", "inhuman"],
        default="multi",
        help="Difficulty tier evaluation mode: 'multi' (default, runs all 4 tiers), or specific tier ('all', 'number', 'greek', 'inhuman')",
    )
    run_parser.add_argument(
        "--csv",
        default="samples/4k/uuzs_bench.csv",
        help="Path to uuzs_bench.csv benchmark ground-truth file",
    )
    run_parser.add_argument(
        "--workers",
        "-w",
        type=int,
        default=None,
        help="Number of parallel worker processes (default: CPU cores count)",
    )
    run_parser.add_argument(
        "--rate-step",
        type=float,
        default=None,
        help="Clock rate step size (e.g. 0.05, 0.02, 0.01)",
    )
    run_parser.add_argument(
        "--output-dir",
        "-o",
        default="results",
        help="Output directory for reports and charts (default: results)",
    )
    run_parser.add_argument(
        "--no-plots",
        action="store_true",
        help="Disable Matplotlib diagnostic chart generation",
    )
    run_parser.add_argument(
        "--no-export-predictions",
        action="store_true",
        help="Skip the per-map predictions CSV (written by default).",
    )
    run_parser.add_argument(
        "--predictions-file",
        default="predictions.csv",
        metavar="PATH",
        help=(
            "Filename (or path) for the per-map predictions CSV "
            "(default: predictions.csv inside the output directory). The CSV pairs "
            "each evaluated beatmap with the ground truth "
            "(path,name,pattern,subPattern,expected,got,numericdifficulty,delta,deltaAbs,"
            "ndinscope,deltainscope,deltaAbsinscope); "
            "columns path/name/pattern/subPattern are join-compatible with "
            "samples/4k/uuzs_bench.csv. In multi-tier mode a per-tier file is "
            "written into each tier folder plus a merged file at the output root."
        ),
    )
    run_parser.add_argument(
        "--declare-scope",
        action="append",
        default=None,
        metavar="SPEC",
        dest="declare_scope",
        help=(
            "Declare a difficulty scope the algorithm does not attempt, e.g. "
            "'pattern:ln' (skip LN-family charts) or 'nd<10.5' (skip everything "
            "below ND 10.5). Repeatable; multiple declarations combine with OR "
            "semantics. A declaration NEVER changes the headline score or the six "
            "dimensions -- those always span the full dataset. It adds a secondary "
            "in-scope Accuracy reading (with its own anchors), the scope "
            "description, and how much real-world usage the scope gives up. "
            "Terms: pattern:<name>[,<name>...], nd<X / nd<=X (skip below X; keep "
            "ND >= X), nd>X / nd>=X (skip above X; keep ND <= X). '<'/'<=' and "
            "'>'/'>=' behave identically; 'difficulty' is accepted as an alias "
            "of 'nd'."
        ),
    )

    # Dataset Filtering & Domain Debugging Flags
    run_parser.add_argument(
        "--patterns",
        type=str,
        default=None,
        help="Comma-separated pattern whitelist to evaluate (e.g. 'speed,jack,tech,stamina')",
    )
    run_parser.add_argument(
        "--exclude-patterns",
        type=str,
        default=None,
        help="Comma-separated pattern blacklist to exclude (e.g. 'ln' or 'ln,course')",
    )
    run_parser.add_argument(
        "--min-diff",
        "--min-expected",
        type=float,
        default=None,
        dest="min_expected",
        help="Minimum expected difficulty / ND (inclusive, e.g. 10.5)",
    )
    run_parser.add_argument(
        "--max-diff",
        "--max-expected",
        type=float,
        default=None,
        dest="max_expected",
        help="Maximum expected difficulty / ND (inclusive, e.g. 18.5)",
    )
    run_parser.add_argument(
        "--filter-name",
        type=str,
        default=None,
        help="Case-insensitive substring filter for beatmap name",
    )
    run_parser.add_argument(
        "--filter-preset",
        choices=sorted(FILTER_PRESETS),
        default=None,
        help=(
            "DEBUG ONLY: preset that physically shrinks the dataset. Every "
            "headline metric (CAB score and all six dimensions) is then computed "
            "over a different population, so the result is NOT comparable with "
            "the published leaderboard. For 'this algorithm only targets slice X' "
            "reporting, use --declare-scope instead: it keeps all headline metrics "
            "on the full dataset and adds a secondary in-scope Accuracy reading. "
            "Known presets: "
            + "; ".join(f"'{k}': {v.get('description', '')}" for k, v in sorted(FILTER_PRESETS.items()))
        ),
    )
    run_parser.add_argument(
        "--sp-source",
        type=str,
        default=None,
        help=(
            "Results directory (or JSON file) whose Speed dimension (SP) is "
            "inherited instead of being measured in this run. Used by fast "
            "drop-in replacements (e.g. sunny-rs) so SP keeps reflecting the "
            "original algorithm's real speed. Example: results_sunny_standard"
        ),
    )
    run_parser.add_argument(
        "--sp-only",
        action="store_true",
        help=(
            "Measure only the 33-chart dan-course speed probe and exit. Produces "
            "an SP provenance file for --sp-source, so a fast port can inherit "
            "the original implementation's real speed without benchmarking the "
            "slow original end to end."
        ),
    )

    return parser


def _report_dan_overlay_msd(root_dir: Path) -> None:
    """Warn when Dan-Overlay's msd.exe cannot be executed on this host.

    Dan-Overlay ships only ``tools/bin/msd.exe``.  On Linux it must go through
    Wine; without Wine the bridge returns ``None`` and the adapter silently
    falls back to the pure-ONNX ``sr``, so the algorithm's readings change with
    no error anywhere.  The check runs in the parent process because the
    persistent server's stderr is discarded.
    """
    if sys.platform.startswith("win"):
        return
    exe = root_dir / "test_algorithms" / "Dan-Overlay" / "tools" / "bin" / "msd.exe"
    native = exe.with_suffix("")
    if native.is_file():
        print(f"[cab] Dan-Overlay MSD runtime: native {native}")
        return
    wine = shutil.which("wine64") or shutil.which("wine")
    if wine is None:
        sys.stderr.write(
            "[cab] WARNING: Dan-Overlay needs Wine to run msd.exe and none was "
            "found. It falls back to the pure-ONNX sr, so these readings are "
            "NOT comparable with a Wine-enabled run.\n"
        )
    else:
        print(f"[cab] Dan-Overlay MSD runtime: {wine} {exe}")


def _scope_export_kwargs(result, scope_source=None) -> dict:
    """Declared-scope payload for the per-map predictions export.

    The scope reading is a run-level concept: the secondary in-scope calibration
    is fitted once per run, so every exported CSV (including per-tier ones in
    multi-tier mode) marks the same beatmaps as out of scope and reuses the same
    in-scope values. ``scope_source`` lets tier exports reuse the master
    result's fit; it defaults to ``result`` itself.
    """
    source = result if scope_source is None else scope_source
    scope = getattr(source, "scope_report", None)
    if scope is None or not scope.declarations:
        return {}
    kept = list(scope.in_scope_samples)
    preds = list(getattr(source, "in_scope_predictions", None) or [])
    if len(preds) != len(kept):
        # No secondary fit was produced (e.g. too few charts to fit anchors):
        # mark the whole slice as unavailable rather than pairing mismatched rows.
        return {"in_scope_calibrated": {}}
    return {"in_scope_calibrated": {s.name: float(p) for s, p in zip(kept, preds)}}


def _export_tier_predictions(result, output_path: Path, scope_source=None) -> None:
    """Write the per-map predictions CSV for a single benchmark result.

    Results produced before the ``samples`` field existed carry no per-map
    payload; skip silently in that case rather than emitting an empty file that
    would look like a legitimate zero-map run.
    """
    samples = getattr(result, "samples", None) or []
    if not samples:
        return
    export_predictions_csv(
        samples=samples,
        calibrated=result.accuracy.calibrated_predictions,
        output_path=output_path,
        raw=getattr(result, "raw_predictions", None) or None,
        **_scope_export_kwargs(result, scope_source),
    )


def _export_merged_predictions(result: "MultiTierBenchmarkResult", output_path: Path) -> None:
    """Write one CSV covering the full dataset (master result) in multi-tier mode."""
    _export_tier_predictions(result.master_result, output_path)


def _export_tier_ledgers(result, output_dir: Path) -> list[Path]:
    """Write the dynamic-probe ledgers (rate curves / jitter / short-LN).

    These carry the per-map readings that used to live only in memory.  Runs
    from before the ledger fields existed simply write nothing.
    """
    if not (getattr(result, "samples", None) or []):
        return []
    written = export_probe_ledgers(result, output_dir)
    # The metric ledger is the offline-rescoring payload: raw per-map readings
    # plus every conversion constant.  Written after the probes so the four
    # files sit together (see cab/report/metric_ledger.py).
    written.extend(export_metric_ledger(result, output_dir))
    return written


def _resolve_scale(args, default: str) -> str:
    """Resolve ``--scale`` and warn when it contradicts the algorithm's native scale.

    ``--scale`` is not a harmless label: choosing ``nd`` for a star-rating
    algorithm disables PAVA calibration entirely, and choosing ``daniel`` for one
    adds a -0.5 offset it never had.  The run still completes, with silently
    wrong accuracy.
    """
    if getattr(args, "scale", "auto") == "auto":
        return default
    chosen = args.scale
    if chosen != default:
        effect = {
            "nd": "calibration is DISABLED (raw algorithm units are compared with ND)",
            "daniel": "a -0.5 daniel->ND offset is applied",
            "non-nd": "PAVA calibration is enabled",
        }.get(chosen, "the mapping changes")
        print(
            f"[warn] --scale {chosen} overrides this algorithm's native scale "
            f"({default}): {effect}.",
            file=sys.stderr,
        )
    return chosen


def main():
    parser = build_parser()
    if len(sys.argv) == 1:
        # Default to 'run' if no subcommand given
        args = parser.parse_args(["run"] + sys.argv[1:])
    else:
        args = parser.parse_args()

    if args.subcommand != "run":
        parser.print_help()
        sys.exit(1)

    # 1. Instantiate Adapter
    adapter: BaseAdapter
    algo_key = args.algorithm.lower().replace("_", "-")
    root_dir = Path(__file__).resolve().parent.parent

    # Declared-scope specs an algorithm ships with, used only when the caller
    # passes no --declare-scope.  A declaration never touches the headline
    # score (see cab.metrics.abstention); it only adds the secondary in-scope
    # Accuracy reading.  Keep these traceable to what the author actually
    # publishes -- do not invent a domain an algorithm never claimed.
    default_scope_specs: tuple[str, ...] = ()

    if args.cmd:
        scale = _resolve_scale(args, "non-nd")
        algo_name = (
            args.algo_name
            or (
                args.algorithm
                if args.algorithm.lower() not in ("daniel", "cli")
                else "CustomAlgorithm"
            )
        )
        adapter = CliAdapter(
            name=algo_name,
            cmd_template=args.cmd,
            scale_type=scale,
            server_cmd=getattr(args, "server_cmd", None),
        )
    elif algo_key == "daniel":
        scale = _resolve_scale(args, "daniel")
        adapter = DanielAdapter(daniel_root=args.daniel_path, scale_type=scale)
    elif algo_key in ("mixed", "mixed-estimator"):
        scale = _resolve_scale(args, "nd")
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="Mixed",
            cmd_template=f"node {cli_runner} --algo mixed --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo mixed --server",
        )
    elif (
        algo_key in ("minacalc", "msd", "etterna")
        or algo_key.startswith(("minacalc-", "minacalc_", "msd-", "msd_", "etterna-", "etterna_"))
    ):
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        sub = ""
        for prefix in ("minacalc-", "minacalc_", "msd-", "msd_", "etterna-", "etterna_"):
            if algo_key.startswith(prefix):
                sub = algo_key[len(prefix):]
                break
        if "68" in sub:
            ver_name = "0.68.0"
        elif "70" in sub:
            ver_name = "0.70.0"
        elif "74" in sub:
            ver_name = "0.74.0"
        elif "75" in sub:
            ver_name = "0.75.0"
        else:
            ver_name = "0.72.3"
        adapter = CliAdapter(
            name=f"MinaCalc-{ver_name}",
            cmd_template=f"node {cli_runner} --algo {algo_key} --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo {algo_key} --server",
        )
    elif algo_key in ("companella", "companella-estimator"):
        # Companella emits a *dan tier* value (dan_model.onnx, clamped to 1..20
        # then +1), with Alpha ≈ 11.0 and Delta ≈ 14.0 directly matching
        # the Numeric Difficulty (ND) coordinate system (NOT Daniel's +0.5 offset).
        # We treat Companella as native ND ("nd").
        scale = _resolve_scale(args, "nd")
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="Companella",
            cmd_template=f"node {cli_runner} --algo companella --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo companella --server",
        )
    elif algo_key in ("dan-overlay", "danoverlay"):
        # Dan-Overlay emits DP on the 1-20 dan coordinate system centered on
        # Daniel's offset (Alpha = 11.5; see its rank_engine.py), NOT raw
        # non-nd.  The "daniel" channel applies the standard
        # "daniel - 0.5 = ND" shift.  Without it the ~0.5 offset rides high
        # absorbed by the PAVA fit instead (measured on the 832-map standard
        # set: wRMSE 1.37 with the standard shift vs 1.09 with PAVA).
        scale = _resolve_scale(args, "daniel")
        cli_runner = root_dir / "cab/adapter/dan_overlay_cli.py"
        # ISOR's published domain (src/08_isor_engine/ISOR.md §0 and §12):
        # strictly 4K rice -- "Long Note (LN) maps are unsupported and
        # explicitly rejected", i.e. no LN charts.  The numeric band is the
        # slice its own VSRG write-up reports on ("Tiers 11.0-17.0, Core
        # Competitive Arena"), taken with a 0.5 margin on both ends.
        default_scope_specs = ("pattern:ln nd<10.5 nd>16.5",)
        adapter = CliAdapter(
            name="Dan-Overlay",
            cmd_template=f"{_PY} {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --server",
            # msd.exe ships as a Windows PE only.  On Linux it runs through
            # Wine, whose start-up cost (measured ~5 s per call on a cold
            # server, versus ~11 ms of actual MinaCalc work) would otherwise be
            # charged to the algorithm by the SP dimension.  The probe reports
            # that cost so it can be subtracted; it reports 0.0 on Windows,
            # where the binary runs natively.
            overhead_probe_cmd=f"{_PY} {cli_runner} --probe-overhead",
        )
        _report_dan_overlay_msd(root_dir)
    elif algo_key in ("sunny", "star-rating-rebirth", "rebirth"):
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/sunny_cli.py"
        adapter = CliAdapter(
            name="Star-Rating-Rebirth",
            cmd_template=f"{_PY} {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --server",
        )
    elif algo_key in ("sunny-rs", "rebirth-rs", "rosupp-old"):
        # Faithful Rust port of Star-Rating-Rebirth (rosu-pp @ f0663cf).
        # Numbers match the Python original to ~1e-6 but run ~40x faster.
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/rosupp_cli.py"
        adapter = CliAdapter(
            name="Star-Rating-Rebirth",
            cmd_template=f"{_PY} {cli_runner} --variant sunny-rs {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --variant sunny-rs --server",
        )
    elif algo_key in ("arily", "rosupp", "rosu-pp"):
        # Arily's modified formula (rosu-pp main), owner of the ppy-sb server.
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/rosupp_cli.py"
        adapter = CliAdapter(
            name="Arily",
            cmd_template=f"{_PY} {cli_runner} --variant arily {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --variant arily --server",
        )
    elif algo_key in ("treestar", "osu-star-web", "tree-star"):
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="osu-star-web",
            cmd_template=f"node {cli_runner} --algo treestar --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo treestar --server",
        )
    elif algo_key in ("interlude", "yavsrg"):
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="YAVSRG-Interlude",
            cmd_template=f"node {cli_runner} --algo interlude --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo interlude --server",
        )
    elif algo_key in ("osu-official", "osu-lazer", "lazer"):
        # Official osu!lazer mania difficulty: osu.Game is built from a local
        # checkout of ppy/osu (master) — see csharp_runners/osu_official/.
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/osu_official_cli.py"
        adapter = CliAdapter(
            name="osu!lazer (Official)",
            cmd_template=f"{_PY} {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --server",
        )
    elif algo_key in ("osu-hold-fix", "osu-mania-hold-fix", "hold-fix"):
        # molneya/osu, branch ``holds-fix``: the mania strain evaluator awards
        # a progressive hold bonus from hold duration / hold-end distance
        # instead of the flat 1.25x ``holdFactor`` in ppy/osu master.
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/osu_hold_fix_cli.py"
        adapter = CliAdapter(
            name="osu!mania (holds-fix)",
            cmd_template=f"{_PY} {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --server",
        )
    elif algo_key in ("osu-mania-former", "osu-former", "mania-former"):
        # The mania algorithm as it stood immediately before ppy/osu#24109
        # ("Adjust LN value scaling...", merged 2023-08-17): built from ppy/osu
        # tag 2023.815.0 — see csharp_runners/osu_mania_former/.  This is a
        # historical variant for measuring what that LN fix changed, NOT the
        # current official algorithm; rice maps are essentially unchanged,
        # LN-heavy maps read noticeably higher.
        scale = _resolve_scale(args, "non-nd")
        cli_runner = root_dir / "cab/adapter/osu_mania_former_cli.py"
        adapter = CliAdapter(
            name="osu!mania (pre-#24109)",
            cmd_template=f"{_PY} {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"{_PY} {cli_runner} --server",
        )
    elif algo_key == "cli":
        if not args.cmd:
            sys.stderr.write("Error: --cmd template is required when --algorithm is 'cli'\n")
            sys.exit(2)
        adapter = CliAdapter(name="CustomCLI", cmd_template=args.cmd, scale_type=args.scale)
    else:
        known = [
            "daniel", "mixed", "minacalc", "companella", "dan-overlay",
            "sunny", "sunny-rs", "arily", "treestar", "osu-official",
            "osu-mania-former", "osu-hold-fix", "interlude", "cli",
        ]
        sys.stderr.write(
            f"Unknown algorithm: '{args.algorithm}'. Supported: {', '.join(known)}\n"
        )
        sys.exit(2)

    # 2. Prepare Config
    config_kwargs = {}
    if args.workers is not None:
        config_kwargs["workers"] = args.workers
    if args.rate_step is not None:
        config_kwargs["rate_step"] = args.rate_step
    if args.csv:
        config_kwargs["csv_path"] = args.csv
    if args.output_dir:
        config_kwargs["output_dir"] = args.output_dir
    sp_source = getattr(args, "sp_source", None)
    # SP provenance is opt-in only (--sp-source <dir>): the archive is a
    # *measurement of an external algorithm*, so it does not ship with the
    # benchmark and is never wired up implicitly.  When an algorithm is a
    # drop-in replacement for a slower one (e.g. a Rust port), whoever runs the
    # comparison supplies the original's archive explicitly.
    if sp_source:
        config_kwargs["sp_source"] = sp_source
    config_kwargs["scale_type"] = adapter.scale_type

    # Dataset Filtering & Domain Slicing
    if args.patterns:
        config_kwargs["patterns"] = [p.strip() for p in args.patterns.split(",") if p.strip()]
    if args.exclude_patterns:
        config_kwargs["exclude_patterns"] = [
            p.strip() for p in args.exclude_patterns.split(",") if p.strip()
        ]
    if args.min_expected is not None:
        config_kwargs["min_expected"] = args.min_expected
    if args.max_expected is not None:
        config_kwargs["max_expected"] = args.max_expected
    if args.filter_name:
        config_kwargs["name_filter"] = args.filter_name
    if args.filter_preset:
        config_kwargs["filter_preset"] = args.filter_preset
        sys.stderr.write(
            f"[!] --filter-preset {args.filter_preset!r} is a DEBUG tool: it physically "
            "shrinks the dataset, so every headline metric below (CAB score and all six "
            "dimensions) covers a different population and is NOT comparable with the "
            "published leaderboard.\n"
            "    For 'this algorithm only targets slice X' reporting, use --declare-scope "
            "instead: all headline metrics stay on the full dataset, and a secondary "
            "in-scope Accuracy reading is added.\n"
        )
    if getattr(args, "tier", None):
        config_kwargs["tier_mode"] = args.tier

    # Declared difficulty scope ("what this algorithm does not attempt").
    # Purely informational: it never alters the headline score.
    raw_scope_specs = getattr(args, "declare_scope", None) or []
    scope_source = "command line"
    if not raw_scope_specs and default_scope_specs:
        raw_scope_specs = list(default_scope_specs)
        scope_source = "built-in default for this algorithm"
        sys.stderr.write(
            f"[i] Applying the built-in declared scope for {adapter.name} "
            f"({'; '.join(default_scope_specs)}). Headline metrics stay on the "
            "full dataset; a secondary in-scope Accuracy reading is added. "
            "Pass --declare-scope to override.\n"
        )
    if raw_scope_specs:
        from cab.metrics.abstention import parse_declaration

        try:
            config_kwargs["scope_declarations"] = tuple(
                parse_declaration(spec) for spec in raw_scope_specs
            )
        except ValueError as exc:
            raise SystemExit(
                f"Invalid --declare-scope value (from {scope_source}): {exc}"
            ) from exc

    config = BenchConfig.create(mode=args.mode, **config_kwargs)

    # 3. Execute Suite
    suite = BenchmarkSuite(adapter=adapter, config=config)

    # Speed-only probe: measure just the dan-course speed set and exit.  Lets a
    # fast port (e.g. sunny-rs) inherit the *original* implementation's real
    # speed via --sp-source without running the slow original end to end.
    if getattr(args, "sp_only", False):
        from cab.metrics.speed_metric import evaluate_speed
        from cab.runner.suite import _is_speed_probe

        probe = [s for s in suite.dataset.get_accuracy_samples() if _is_speed_probe(s)]
        if not probe:
            sys.stderr.write("Error: no speed-probe charts found in the dataset.\n")
            sys.exit(2)

        # Run through the same worker path the benchmark uses.  Calling the
        # adapter from the main process looked equivalent but was not: for
        # Dan-Overlay the persistent server failed to come up there, so every
        # chart fell back to a one-shot process (which reloads the model each
        # time) and the measured cost came out ~15x too high.
        import multiprocessing as mp

        from cab.runner.worker import init_worker, run_calc_path_timed, run_measure_overhead

        adapter_type, adapter_kwargs = suite._get_worker_spec()
        sp_paths = [str(s.file_path) for s in probe]
        # "spawn" (not "fork"): fork is unavailable on Windows, and this is the
        # same start method ``BenchmarkSuite.run_all`` already uses, so the
        # probe measures exactly the worker the benchmark would use.  It costs
        # one extra interpreter bootstrap for a single-worker pool.
        ctx = mp.get_context("spawn")
        with ctx.Pool(
            processes=1,
            initializer=init_worker,
            initargs=(adapter_type, adapter_kwargs),
        ) as sp_pool:
            sp_pool.map(run_calc_path_timed, sp_paths[:1])  # warm-up, discarded
            # Baseline from the same worker/server, after warm-up.
            _ovh_raw = sp_pool.map(run_measure_overhead, [None])
            sp_outputs = sp_pool.map(run_calc_path_timed, sp_paths)

        timings = []
        for sample, (reading, elapsed_ms, _err) in zip(probe, sp_outputs):
            if reading is not None:
                timings.append((elapsed_ms, sample.load_beatmap().duration_seconds))

        # Compatibility-layer overhead: a fixed per-invocation cost that only
        # exists on this platform (Wine start-up for Dan-Overlay's msd.exe).
        # Subtracting it is what makes the reading comparable to a native run.
        _ovh_ms = float(_ovh_raw[0] or 0.0) if _ovh_raw else 0.0
        if _ovh_ms > 0.0:
            print(
                f"Compatibility-layer overhead: {_ovh_ms:.1f} ms per invocation "
                f"subtracted (not part of the algorithm's cost)"
            )

        # ── Cross-machine baseline (MinaCalc), same probe, same session ──
        # rho = v_algo / v_baseline cancels the machine's speed and the current
        # load state, so an sp-only archive stays comparable across machines.
        _base_median: float | None = None
        _is_base_algo = (adapter.name or "").lower() in (
            "minacalc",
            "minacalc-0.72",
            "minacalc-0.72.3",
            "minacalc 515",
        )
        if _is_base_algo:
            if timings:
                _base_median = evaluate_speed(timings).median_speed_ms_per_s
        else:
            try:
                _runner = (
                    Path(__file__).resolve().parent.parent
                    / "cab/adapter/node_runners/estimator_cli.mjs"
                )
                _base_kwargs = {
                    "name": "MinaCalc-0.72.3 (SP baseline)",
                    "cmd_template": f"node {_runner} --algo minacalc --file {{input}}",
                    "scale_type": "non-nd",
                    "timeout_seconds": 300.0,
                    "server_cmd": f"node {_runner} --algo minacalc --server",
                    "overhead_probe_cmd": None,
                }
                with ctx.Pool(
                    processes=1,
                    initializer=init_worker,
                    initargs=("cli", _base_kwargs),
                ) as _base_pool:
                    _base_pool.map(run_calc_path_timed, sp_paths[:1])  # warm-up
                    _base_outputs = _base_pool.map(run_calc_path_timed, sp_paths)
                _base_timings = []
                for _s, (_bd, _bdt, _berr) in zip(probe, _base_outputs):
                    if _bd is None:
                        continue
                    _base_timings.append((_bdt, _s.load_beatmap().duration_seconds))
                if _base_timings:
                    _base_median = evaluate_speed(_base_timings).median_speed_ms_per_s
                    print(f"SP baseline (MinaCalc): {_base_median:.4f} ms/s median")
            except Exception as _bexc:
                print(f"[warn] SP baseline unavailable ({_bexc})")

        speed = evaluate_speed(
            timings,
            platform_overhead_ms=_ovh_ms,
            baseline_ms_per_s=_base_median,
            baseline_algorithm="MinaCalc-0.72.3" if _base_median else "",
        )
        # A probe where nothing succeeded used to archive speed_score=100 with
        # evaluated_count=0, and --sp-source later inherited that perfect score.
        # Refuse to write an empty archive.
        if speed.evaluated_count == 0 or speed.speed_score is None:
            raise RuntimeError(
                f"--sp-only measured 0/{len(probe)} charts successfully; "
                "refusing to write a speed archive with no data"
            )
        sp_dir = Path(config.output_dir).resolve()
        sp_dir.mkdir(parents=True, exist_ok=True)
        _sp_target = sp_dir / "cab_benchmark_result.json"
        if _sp_target.is_file():
            try:
                _existing = json.loads(_sp_target.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                _existing = {}
            if _existing.get("mode") not in (None, "sp_only"):
                raise RuntimeError(
                    f"{_sp_target} already holds a full '{_existing.get('mode')}' run; "
                    "--sp-only would replace it with a speed-only payload. "
                    "Use a separate --output-dir."
                )
        payload = {
            "benchmark": "CAB-VA",
            "algorithm": adapter.name,
            "mode": "sp_only",
            "probe_charts": len(probe),
            # Provenance: an SP reading captured here may later be reused via
            # --sp-source on a different machine (e.g. a native Windows run of
            # Dan-Overlay, to escape the Wine tax).  Without the environment
            # block there is no way to tell afterwards which CPU/OS produced
            # the number, and SP is only comparable within one environment.
            "environment": collect_environment_info(),
            "dimension_6_speed": {
                "average_speed_ms_per_s": speed.average_speed_ms_per_s,
                "median_speed_ms_per_s": speed.median_speed_ms_per_s,
                # p95 / max are exported so a degraded adapter is visible at a
                # glance: a median in the same order as p95/max means every
                # chart paid the full cost (e.g. Dan-Overlay reloading its model
                # per chart when the probe runs in a cold process).
                "p95_speed_ms_per_s": speed.p95_speed_ms_per_s,
                "max_speed_ms_per_s": speed.max_speed_ms_per_s,
                "realtime_factor": speed.realtime_factor,
                "speed_score": speed.speed_score,
                "evaluated_count": speed.evaluated_count,
                # Non-zero only when a compatibility layer was in the path and
                # its fixed cost was subtracted (see --probe-overhead).
                "platform_overhead_ms": speed.platform_overhead_ms,
                # Cross-machine reading: ratio = v / v_MinaCalc on the same
                # probe.  Archived so a later --sp-source reuse re-scores with
                # the current curve instead of the archived absolute score.
                "baseline_ratio": speed.baseline_ratio,
                "baseline_ms_per_s": speed.baseline_ms_per_s,
                "baseline_algorithm": speed.baseline_algorithm,
            },
        }
        (sp_dir / "cab_benchmark_result.json").write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(
            f"SP probe complete: {speed.speed_score:.2f}/100 @ "
            f"{speed.median_speed_ms_per_s:.4f} ms/s median "
            f"(p95 {speed.p95_speed_ms_per_s:.4f}, max {speed.max_speed_ms_per_s:.4f}) "
            f"({speed.evaluated_count}/{len(probe)} charts) -> {sp_dir}"
        )
        return

    # 4. Generate Reports and Artifacts
    # Create the output directory *before* the run: a typo in -o (or a
    # read-only mount) used to surface only after hours of computation.
    out_dir = Path(config.output_dir).resolve()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        _probe = out_dir / ".cab_write_test"
        _probe.write_text("ok", encoding="utf-8")
        _probe.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"output directory is not writable: {out_dir} ({exc})") from None

    # Run marker: an interrupted run leaves a stale artifact tree that looks
    # exactly like a fresh one.  `.running` carries the
    # identity of this invocation; `.done` is only written on full success.
    run_id = f"{time.strftime('%Y%m%dT%H%M%S')}-{os.getpid()}"
    _running_path = out_dir / ".running"
    _running_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "algorithm": suite.adapter.name,
                "mode": config.mode,
                "workers": config.workers,
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "argv": sys.argv[1:],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    result = suite.run_all(verbose=True)

    if isinstance(result, MultiTierBenchmarkResult):
        # 1. Output individual tier folders & reports
        for t_id, tr in result.tier_results.items():
            t_def = TIER_DEFINITIONS.get(t_id)
            dir_name = t_def.dir_name if t_def else t_id
            tier_dir = out_dir / dir_name
            tier_dir.mkdir(parents=True, exist_ok=True)

            tier_imgs = {}
            if not args.no_plots:
                tier_imgs = generate_all_plots(tr, tier_dir)

            export_to_json(tr, tier_dir / "cab_benchmark_result.json")
            generate_markdown_report(tr, tier_dir / "CAB_BENCHMARK_REPORT.md", image_paths=tier_imgs)
            generate_markdown_report(
                tr, tier_dir / "CAB_BENCHMARK_REPORT_EN.md", image_paths=tier_imgs, lang="en"
            )

            if not args.no_export_predictions:
                _export_tier_predictions(
                    tr,
                    tier_dir / args.predictions_file,
                    scope_source=result.master_result,
                )
                _export_tier_ledgers(tr, tier_dir)

        # 2. Output Root Multi-Tier Artifacts & Summary
        multi_imgs = {}
        if not args.no_plots:
            multi_imgs = generate_all_plots(result, out_dir)

        summary_json = out_dir / "cab_multi_tier_summary.json"
        export_multi_tier_to_json(result, summary_json)

        summary_md = out_dir / "CAB_MULTI_TIER_SUMMARY.md"
        generate_multi_tier_summary_report(result, summary_md, image_paths=multi_imgs)
        summary_md_en = out_dir / "CAB_MULTI_TIER_SUMMARY_EN.md"
        generate_multi_tier_summary_report(result, summary_md_en, image_paths=multi_imgs, lang="en")

        # Root master report for backwards compatibility
        root_json = out_dir / "cab_benchmark_result.json"
        export_to_json(result.master_result, root_json)
        root_md = out_dir / "CAB_BENCHMARK_REPORT.md"
        generate_markdown_report(result.master_result, root_md, image_paths=multi_imgs)
        root_md_en = out_dir / "CAB_BENCHMARK_REPORT_EN.md"
        generate_markdown_report(result.master_result, root_md_en, image_paths=multi_imgs, lang="en")

        print(f"\nArtifacts saved successfully:")
        print(f"  • Multi-Tier Summary: {summary_md}")
        print(f"  • Multi-Tier JSON:    {summary_json}")
        if "multi_tier_comparison" in multi_imgs:
            print(f"  • Multi-Tier Chart:   {multi_imgs['multi_tier_comparison']}")
        print(f"  • Master Report:      {root_md}")
        print(f"  • Tier Folders:       all/, number_dan/, greek_dan/, inhuman_dan/")

        if not args.no_export_predictions:
            merged = out_dir / args.predictions_file
            merged.parent.mkdir(parents=True, exist_ok=True)
            _export_merged_predictions(result, merged)
            print(f"  • Predictions CSV:    {merged}")
        # Ledgers are the audit trail for the dynamic dimensions; they must not
        # disappear just because the caller disabled the CSV export.
        for ledger in _export_tier_ledgers(result.master_result, out_dir):
            print(f"  • Probe ledger:       {ledger}")
    else:
        image_paths = {}
        if not args.no_plots:
            image_paths = generate_all_plots(result, out_dir)

        json_path = out_dir / "cab_benchmark_result.json"
        export_to_json(result, json_path)

        md_path = out_dir / "CAB_BENCHMARK_REPORT.md"
        generate_markdown_report(result, md_path, image_paths=image_paths)
        generate_markdown_report(
            result, out_dir / "CAB_BENCHMARK_REPORT_EN.md", image_paths=image_paths, lang="en"
        )

        pred_path = None
        if not args.no_export_predictions:
            pred_path = out_dir / args.predictions_file
            pred_path.parent.mkdir(parents=True, exist_ok=True)
            _export_tier_predictions(result, pred_path)
        _export_tier_ledgers(result, out_dir)

        # 5. Print Rich Summary Table
        print_console_report(result)
        print(f"\nArtifacts saved successfully:")
        print(f"  • JSON Result: {json_path}")
        print(f"  • Markdown Report: {md_path}")
        for k, p in image_paths.items():
            print(f"  • Chart ({k}): {p}")
        if pred_path:
            print(f"  • Predictions CSV: {pred_path}")

    # Success marker last, so a crash mid-export never looks like a good run.
    (out_dir / ".done").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    _running_path.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.", file=sys.stderr)
        sys.exit(130)
    except (RuntimeError, ValueError, FileNotFoundError, OSError) as exc:
        # Never exit 0 on a failed run: the batch scripts key `.std_done` off
        # the exit code, so a silent success skipped that algorithm forever.
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(3)
