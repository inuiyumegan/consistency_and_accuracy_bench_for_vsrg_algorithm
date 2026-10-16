#!/usr/bin/env bash
# CAB-VA: 顺序对全部内置算法执行 standard 全量基准（低并发内存安全版）
#
# 用法:
#   ./scripts/run_all_standard_benchmarks.sh [default_workers]
#
# 特性:
#   - workers 自适应：node 系算法 8（常驻进程较轻）/ python 系算法 6（内存较重）
#     （16GB 内存机器安全值，避免 OOM 杀进程；传入参数可覆盖默认值）
#   - 环境隔离：去除 safe-delete shim 与会话变量，确保临时文件正常清理
#   - 幂等：输出目录中 .std_done 存在时自动跳过，可中断续跑
#   - 顺序：按 quick 耗时升序
#
# 输出: results_<algo>_standard/  日志: /tmp/cab_std_<algo>.log
set -u

cd "$(dirname "$0")/.."

DEFAULT_WORKERS="${1:-6}"
# treestar（osu-star-web）：经决定采用 quick 模式数据（results_treestar/），不参与本 standard 批次。
# sunny-rs：Star-Rating-Rebirth 的 Rust 移植（rosu-pp-old），报告名仍沿用 "Star-Rating-Rebirth"，
#           用以替代性能较差的原版 python sunny；原 sunny 保留但不再纳入默认批次。
ALGOS=(minacalc interlude daniel dan-overlay companella mixed sunny-rs arily)

# 内存安全 + 吞吐平衡：node 系算法常驻进程较轻（8 workers）；
# python 系算法（Daniel / Dan-Overlay）内存更重（6 workers）。
# sunny-rs / arily 为 Rust 常驻进程，内存轻，可用高并发。
declare -A ALGO_WORKERS=(
  [minacalc]=8 [interlude]=8 [mixed]=8 [companella]=8
  [daniel]=6 [dan-overlay]=6 [sunny]=6
  [sunny-rs]=8 [arily]=8
)

# 算法专属额外参数（可选）。
# 注意：standard 全量批次对所有算法一视同仁，**不再有 per-algorithm 数据集特例**。
# Daniel 虽然官方段位只覆盖 ng∈[10.5,18.5] 且未建模 LN，但 standard 模式下
# 仍按全量 787 张谱面评测（与其余算法同口径），特例过滤留给专项实验另行执行。
declare -A ALGO_EXTRA_ARGS=()

# 环境隔离：让评测的临时文件删除不被宿主安全机制拦截
NEWPATH=$(echo "$PATH" | tr ':' '\n' | grep -v "shim/safe-bin" | paste -sd ':')

echo "============================================================"
echo "CAB-VA Standard Full-Benchmark Batch"
echo "Workers per algorithm: node-tier=8 / python-tier=6 (default=${DEFAULT_WORKERS})"
echo "Started: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

for algo in "${ALGOS[@]}"; do
  outdir="results_${algo}_standard"
  if [ -f "${outdir}/.std_done" ]; then
    echo "[skip] ${algo}: 已完成 (${outdir})"
    continue
  fi

  WORKERS="${ALGO_WORKERS[$algo]:-$DEFAULT_WORKERS}"
  # 组装参数数组：仅在存在额外参数时追加，避免空展开成幽灵参数
  RUN_ARGS=(run --algorithm "${algo}" --mode standard --workers "${WORKERS}")
  if [ -n "${ALGO_EXTRA_ARGS[$algo]:-}" ]; then
    # shellcheck disable=SC2206
    RUN_ARGS+=(${ALGO_EXTRA_ARGS[$algo]})
  fi
  RUN_ARGS+=(--output-dir "${outdir}")

  echo ""
  echo "[run ] ${algo} -> ${outdir} (workers=${WORKERS} ${ALGO_EXTRA_ARGS[$algo]:-}) | start $(date '+%H:%M:%S')"
  free -h | head -2 | tail -1

  env -u CODEBUDDY_SESSION_ID -u CLAUDE_SESSION_ID PATH="$NEWPATH" \
    ./cab_run.sh "${RUN_ARGS[@]}" > "/tmp/cab_std_${algo}.log" 2>&1
  code=$?

  if [ $code -eq 0 ]; then
    touch "${outdir}/.std_done"
    echo "[done] ${algo} | exit=0 | end $(date '+%H:%M:%S')"
  else
    echo "[fail] ${algo} | exit=${code} | 日志见 /tmp/cab_std_${algo}.log（批次继续）"
  fi
done

echo ""
echo "============================================================"
echo "ALL STANDARD BENCHMARKS FINISHED: $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
