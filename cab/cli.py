"""CAB-VA CLI: Command-Line Interface for the VSRG Difficulty Algorithm Benchmark."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cab.adapter.base import BaseAdapter
from cab.adapter.cli import CliAdapter
from cab.adapter.daniel import DanielAdapter
from cab.report.console import print_console_report
from cab.report.json_export import export_multi_tier_to_json, export_to_json
from cab.report.markdown import generate_markdown_report, generate_multi_tier_summary_report
from cab.report.visualization import generate_all_plots
from cab.runner.config import (
    DEFAULT_TIERS,
    FILTER_PRESETS,
    TIER_DEFINITIONS,
    BenchConfig,
)
from cab.runner.suite import BenchmarkSuite, MultiTierBenchmarkResult


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
        "--mode",
        "-m",
        choices=["quick", "standard", "full"],
        default="standard",
        help="Benchmark preset: standard (default, 100%% full dataset across all dimensions), quick (fast sampling check for dev), full (100 rate steps)",
    )
    run_parser.add_argument(
        "--scale",
        choices=["auto", "ng", "non-ng", "daniel"],
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
        help="Minimum expected difficulty / ng (inclusive, e.g. 10.5)",
    )
    run_parser.add_argument(
        "--max-diff",
        "--max-expected",
        type=float,
        default=None,
        dest="max_expected",
        help="Maximum expected difficulty / ng (inclusive, e.g. 18.5)",
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
            "Preset domain filter. Known presets: "
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

    return parser


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

    if args.cmd:
        scale = "non-ng" if args.scale == "auto" else args.scale
        algo_name = args.algorithm if args.algorithm.lower() not in ("daniel", "cli") else "CustomAlgorithm"
        adapter = CliAdapter(name=algo_name, cmd_template=args.cmd, scale_type=scale)
    elif algo_key == "daniel":
        scale = "daniel" if args.scale == "auto" else args.scale
        adapter = DanielAdapter(daniel_root=args.daniel_path, scale_type=scale)
    elif algo_key in ("mixed", "mixed-estimator"):
        scale = "ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="Mixed",
            cmd_template=f"node {cli_runner} --algo mixed --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo mixed --server",
        )
    elif algo_key in ("minacalc", "msd", "etterna"):
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="MinaCalc-0.72.3",
            cmd_template=f"node {cli_runner} --algo minacalc --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo minacalc --server",
        )
    elif algo_key in ("companella", "companella-estimator"):
        # Companella emits a *dan tier* value (dan_model.onnx, clamped to 1..20
        # then +1) -- the same coordinate system as Daniel, NOT raw non-ng.
        # Using "daniel" makes calibration apply the "daniel - 0.5 = ng" shift;
        # without it the whole scale is offset by ~0.5 and the ng-RMSE explodes
        # (measured: 24.38 -> 1.36 on a 260-map probe).
        scale = "daniel" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="Companella",
            cmd_template=f"node {cli_runner} --algo companella --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo companella --server",
        )
    elif algo_key in ("dan-overlay", "danoverlay"):
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/dan_overlay_cli.py"
        adapter = CliAdapter(
            name="Dan-Overlay",
            cmd_template=f"python3 {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"python3 {cli_runner} --server",
        )
    elif algo_key in ("sunny", "star-rating-rebirth", "rebirth"):
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/sunny_cli.py"
        adapter = CliAdapter(
            name="Star-Rating-Rebirth",
            cmd_template=f"python3 {cli_runner} {{input}}",
            scale_type=scale,
            server_cmd=f"python3 {cli_runner} --server",
        )
    elif algo_key in ("sunny-rs", "rebirth-rs", "rosupp-old"):
        # Faithful Rust port of Star-Rating-Rebirth (rosu-pp @ f0663cf).
        # Numbers match the Python original to ~1e-6 but run ~40x faster.
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/rosupp_cli.py"
        adapter = CliAdapter(
            name="Star-Rating-Rebirth",
            cmd_template=f"python3 {cli_runner} --variant sunny-rs {{input}}",
            scale_type=scale,
            server_cmd=f"python3 {cli_runner} --variant sunny-rs --server",
        )
    elif algo_key in ("arily", "rosupp", "rosu-pp"):
        # Arily's modified formula (rosu-pp main), owner of the ppy-sb server.
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/rosupp_cli.py"
        adapter = CliAdapter(
            name="Arily",
            cmd_template=f"python3 {cli_runner} --variant arily {{input}}",
            scale_type=scale,
            server_cmd=f"python3 {cli_runner} --variant arily --server",
        )
    elif algo_key in ("treestar", "osu-star-web", "tree-star"):
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="osu-star-web",
            cmd_template=f"node {cli_runner} --algo treestar --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo treestar --server",
        )
    elif algo_key in ("interlude", "yavsrg"):
        scale = "non-ng" if args.scale == "auto" else args.scale
        cli_runner = root_dir / "cab/adapter/node_runners/estimator_cli.mjs"
        adapter = CliAdapter(
            name="YAVSRG-Interlude",
            cmd_template=f"node {cli_runner} --algo interlude --file {{input}}",
            scale_type=scale,
            server_cmd=f"node {cli_runner} --algo interlude --server",
        )
    elif algo_key == "cli":
        if not args.cmd:
            sys.stderr.write("Error: --cmd template is required when --algorithm is 'cli'\n")
            sys.exit(2)
        adapter = CliAdapter(name="CustomCLI", cmd_template=args.cmd, scale_type=args.scale)
    else:
        known = [
            "daniel", "mixed", "minacalc", "companella", "dan-overlay",
            "sunny", "sunny-rs", "arily", "treestar", "interlude", "cli",
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
    # Auto-wire SP provenance for drop-in replacements: sunny-rs (fast Rust port
    # of Star-Rating-Rebirth) inherits SP from the original's standard run, so
    # the Speed dimension still reflects the original algorithm's real speed.
    if not sp_source and algo_key in ("sunny-rs", "rebirth-rs", "rosupp-old"):
        candidate = root_dir / "results_sunny_standard"
        sp_source = str(candidate) if candidate.is_dir() else "results_sunny_standard"
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
    if getattr(args, "tier", None):
        config_kwargs["tier_mode"] = args.tier

    config = BenchConfig.create(mode=args.mode, **config_kwargs)

    # 3. Execute Suite
    suite = BenchmarkSuite(adapter=adapter, config=config)
    result = suite.run_all(verbose=True)

    # 4. Generate Reports and Artifacts
    out_dir = Path(config.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

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

        # 2. Output Root Multi-Tier Artifacts & Summary
        multi_imgs = {}
        if not args.no_plots:
            multi_imgs = generate_all_plots(result, out_dir)

        summary_json = out_dir / "cab_multi_tier_summary.json"
        export_multi_tier_to_json(result, summary_json)

        summary_md = out_dir / "CAB_MULTI_TIER_SUMMARY.md"
        generate_multi_tier_summary_report(result, summary_md, image_paths=multi_imgs)

        # Root master report for backwards compatibility
        root_json = out_dir / "cab_benchmark_result.json"
        export_to_json(result.master_result, root_json)
        root_md = out_dir / "CAB_BENCHMARK_REPORT.md"
        generate_markdown_report(result.master_result, root_md, image_paths=multi_imgs)

        print(f"\nArtifacts saved successfully:")
        print(f"  • Multi-Tier Summary: {summary_md}")
        print(f"  • Multi-Tier JSON:    {summary_json}")
        if "multi_tier_comparison" in multi_imgs:
            print(f"  • Multi-Tier Chart:   {multi_imgs['multi_tier_comparison']}")
        print(f"  • Master Report:      {root_md}")
        print(f"  • Tier Folders:       all/, number_dan/, greek_dan/, inhuman_dan/")
    else:
        image_paths = {}
        if not args.no_plots:
            image_paths = generate_all_plots(result, out_dir)

        json_path = out_dir / "cab_benchmark_result.json"
        export_to_json(result, json_path)

        md_path = out_dir / "CAB_BENCHMARK_REPORT.md"
        generate_markdown_report(result, md_path, image_paths=image_paths)

        # 5. Print Rich Summary Table
        print_console_report(result)
        print(f"\nArtifacts saved successfully:")
        print(f"  • JSON Result: {json_path}")
        print(f"  • Markdown Report: {md_path}")
        for k, p in image_paths.items():
            print(f"  • Chart ({k}): {p}")


if __name__ == "__main__":
    main()
