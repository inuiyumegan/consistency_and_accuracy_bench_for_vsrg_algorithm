#!/usr/bin/env bash
set -e

algos=("sunny" "treestar" "interlude")

for algo in "${algos[@]}"; do
    echo "============================================================"
    echo "Starting CAB-VA Quick Benchmark for: ${algo}"
    echo "============================================================"
    out_dir="results_${algo//-/_}"
    ./cab_run.sh run --algorithm "${algo}" --mode quick --output-dir "${out_dir}"
    echo "Finished ${algo} -> saved to ${out_dir}"
done

echo "All remaining benchmarks completed successfully!"
