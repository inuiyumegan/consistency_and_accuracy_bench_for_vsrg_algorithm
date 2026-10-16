"""Script to run quick benchmarks across all 8 supported algorithms and generate a consolidated report."""

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

ALGORITHMS = [
    ("daniel", "results_daniel", "Daniel (Native)"),
    ("mixed", "results_mixed", "Mixed Estimator"),
    ("minacalc", "results_minacalc", "MinaCalc-0.72.3"),
    ("companella", "results_companella", "Companella"),
    ("dan-overlay", "results_dan_overlay", "Dan-Overlay"),
    ("sunny", "results_sunny", "Star-Rating-Rebirth"),
    ("treestar", "results_treestar", "osu-star-web"),
    ("interlude", "results_interlude", "YAVSRG-Interlude"),
]


def run_benchmark(algo_id: str, out_dir: str, display_name: str) -> bool:
    print(f"\n{'='*70}")
    print(f"Running Quick Benchmark: {display_name} ({algo_id}) -> {out_dir}")
    print(f"{'='*70}")
    t0 = time.time()
    cmd = [
        sys.executable,
        "-m", "cab.cli",
        "run",
        "--algorithm", algo_id,
        "--mode", "quick",
        "--output-dir", out_dir,
    ]
    try:
        res = subprocess.run(cmd, check=True)
        dt = time.time() - t0
        print(f"--> [SUCCESS] {display_name} completed in {dt:.1f}s")
        return True
    except subprocess.CalledProcessError as e:
        print(f"--> [ERROR] {display_name} failed with code {e.returncode}")
        return False


def main():
    root = Path(__file__).resolve().parent.parent
    results_summary = {}

    for algo_id, out_dir, display_name in ALGORITHMS:
        out_path = root / out_dir
        success = run_benchmark(algo_id, str(out_path), display_name)
        if success:
            json_file = out_path / "cab_multi_tier_summary.json"
            if json_file.exists():
                with open(json_file, "r", encoding="utf-8") as fp:
                    results_summary[algo_id] = {
                        "name": display_name,
                        "data": json.load(fp),
                        "dir": str(out_path),
                    }

    # Save aggregated summary
    summary_out = root / "results_all_algorithms_summary.json"
    with open(summary_out, "w", encoding="utf-8") as fp:
        json.dump(results_summary, fp, indent=2, ensure_ascii=False)
    print(f"\n[DONE] All algorithms executed. Summary saved to {summary_out}")


if __name__ == "__main__":
    main()
