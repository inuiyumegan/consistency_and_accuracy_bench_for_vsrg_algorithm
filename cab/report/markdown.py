"""Standardized Markdown Benchmark Report Generator for CAB-VA (6 Dimensions, 100-Point Scale)."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from cab.runner.config import (
    DEFAULT_TIERS,
    TIER_ALL,
    TIER_DEFINITIONS,
    TIER_GREEK,
    TIER_INHUMAN,
    TIER_NUMBER,
)
from cab.report.environment import collect_environment_info
from cab.metrics.scoring import (
    BETA_CONTINUITY,
    BETA_INVERSION,
    BETA_JITTER,
    BETA_SHORT_LN,
    BETA_SPEED,
)
from cab.report.profiling import (
    DomainProfile,
    describe_pattern,
    format_pattern_list,
    profile_domains,
)
from cab.runner.suite import BenchmarkRunResult, MultiTierBenchmarkResult


def _render_domain_profile(profile: DomainProfile, algo_name: str) -> list[str]:
    """Render the programmatically-derived strengths / weaknesses section."""
    lines: list[str] = []
    lines.append("#### 算法领域适配画像 (Domain Fitness Profile, 程序化判定)")
    lines.append("")
    if not profile.has_judged:
        lines.append(
            "> [!NOTE]\n"
            "> **无法判定**: 当前样本中没有任何键型达到最小样本量阈值 "
            f"(`>= {profile.min_samples}` 首)，不足以对该算法的擅长/不擅长领域做出可靠结论。\n"
        )
        return lines

    lines.append(
        f"**判定基准**: 以自身各键型 RMSE 的中位数 `{profile.baseline_rmse:.4f}`（ND）作为内部基线，"
        "`比值 = 该键型 RMSE / 基线`；"
        "`<= 0.80` 判为**擅长**，`0.80 ~ 1.20` 判为**达标**，`> 1.20` 判为**不擅长**；"
        f"样本量 `< {profile.min_samples}` 首的键型不参与判定 (标注为样本不足)。\n"
    )
    lines.append(
        "| 判定结论 | 覆盖键型 (相对基线比值) |"
    )
    lines.append("| :--- | :--- |")
    lines.append(f"| 擅长 (Strength) | {format_pattern_list(profile.strengths)} |")
    lines.append(f"| 达标 (Adequate) | {format_pattern_list(profile.adequate)} |")
    lines.append(f"| 不擅长 (Weakness) | {format_pattern_list(profile.weaknesses)} |")
    if profile.insufficient:
        lines.append(f"| 样本不足 (Under-sampled) | {format_pattern_list(profile.insufficient)} |")
    lines.append("")

    label = "，".join(v.described_pattern for v in profile.strengths)
    if profile.strengths:
        lines.append("> [!TIP]")
        lines.append(
            f"> **擅长领域**: `{algo_name}` 在 **{label}** 上的判定误差显著低于其自身平均水准"
            f"（分别 {format_pattern_list(profile.strengths)}），说明算法在这些键型的物理建模上具备优势。"
        )
        lines.append("")

    if profile.weaknesses:
        w_label = "，".join(v.described_pattern for v in profile.weaknesses)
        lines.append("> [!WARNING]")
        lines.append(
            f"> **不擅长领域**: `{algo_name}` 在 **{w_label}** 上的判定误差明显高于其自身平均水准"
            f"（分别 {format_pattern_list(profile.weaknesses)}）。建议优先针对这些键型检查特征提取与密度积分项。"
        )
        lines.append("")

    return lines


def generate_markdown_report(
    result: BenchmarkRunResult,
    output_path: str | Path,
    image_paths: Optional[dict[str, str]] = None,
) -> Path:
    """Generate comprehensive GitHub-flavored Markdown report."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    images = image_paths or {}
    acc = result.accuracy
    inv = result.inversion
    cont = result.continuity
    jit = result.jitter
    sln = result.short_ln
    spd = getattr(result, "speed", None)
    score = result.score

    md_lines: list[str] = []

    # Title & Header
    md_lines.append(f"# CAB-VA 基准测试报告: {result.algorithm_name}")
    md_lines.append(f"**Consistency and Accuracy Bench for VSRG Algorithm (CAB-VA)**\n")
    md_lines.append(f"> **待测算法**: `{result.algorithm_name}`  ")
    md_lines.append(f"> **标尺类型**: `{result.scale_type}`  ")
    md_lines.append(f"> **测试档位**: `{getattr(result, 'tier_name', '全域段 (All Range)')}` (`{getattr(result, 'tier_id', 'all')}`)  ")
    md_lines.append(f"> **测试模式**: `{result.config.mode.upper()}` (步长: $\\Delta r = {result.config.rate_step}$)  ")
    if result.config.has_filters():
        md_lines.append(f"> **数据集过滤**: `{result.filter_summary}`  ")
        if result.sample_stats:
            md_lines.append(f"> **过滤后样本数**: `{result.sample_stats.get('total', len(acc.calibrated_predictions))}` 首  ")
    env = collect_environment_info()
    md_lines.append(f"> **评测环境**: `{env['cpu_model']} · {env['cpu_cores']} 核 · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(速度类指标仅同环境直接可比)*  ")
    md_lines.append(f"> **评估耗时**: `{result.duration_seconds:.2f}` 秒  \n")

    # Score Card
    md_lines.append("## 1. 综合评测总览与评分卡 (Executive Scorecard)")
    md_lines.append("")
    spd_score_str = f"{score.s_speed:.1f}" if spd is not None else "N/A"
    if score.is_subtier:
        md_lines.append(f"| 标准化 CAB-Score (总分) | 准确性 (66.67%) | 抗短面 (20%) | 计算速度 (6.67%) | 抗抖动 (6.67%) |")
        md_lines.append(f"| :---: | :---: | :---: | :---: | :---: |")
        if score.is_out_of_domain:
            md_lines.append(f"| **`N/A`** | N/A | N/A | N/A | N/A |")
        else:
            md_lines.append(
                f"| **`{score.total_score:.1f}`** / 100 | "
                f"{score.s_accuracy:.1f} | {score.s_short_ln:.1f} | "
                f"{spd_score_str} | {score.s_jitter:.1f} |"
            )
        md_lines.append("")
        md_lines.append("> **注**: 当前档位为子难度档位。总分 = ACC × Π[1 − β·(1 − s/100)]，仅对已评测的 ASLN / SPD / AJ 施加折扣；动态倍速维度（抗倒吸 / 局部连续）不在此档位评测，其折扣直接不参与乘积（无需重新归一化）。")
    else:
        md_lines.append(f"| CAB 总分 | 准确性 (主干) | 抗倒吸 (β {BETA_INVERSION*100:g}%) | 抗短面 (β {BETA_SHORT_LN*100:g}%) | 局部连续 (β {BETA_CONTINUITY*100:g}%) | 计算速度 (β {BETA_SPEED*100:g}%) | 抗抖动 (β {BETA_JITTER*100:g}%) |")
        md_lines.append(f"| :---: | :---: | :---: | :---: | :---: | :---: | :---: |")
        s_inv_str = f"{score.s_inversion:.1f}" if score.s_inversion is not None else "-"
        s_cont_str = f"{score.s_continuity:.1f}" if score.s_continuity is not None else "-"
        md_lines.append(
            f"| **`{score.total_score:.1f}`** / 100 | "
            f"{score.s_accuracy:.1f} | {s_inv_str} | {score.s_short_ln:.1f} | "
            f"{s_cont_str} | {spd_score_str} | {score.s_jitter:.1f} |"
        )
    md_lines.append("")

    if "radar" in images:
        md_lines.append(f"![CAB Radar Chart]({images['radar']})\n")

    # 六维明细
    md_lines.append("## 2. 评测技术明细 (Detailed Dimension Results)")
    md_lines.append("")

    # 维度一
    md_lines.append("### 2.1 维度一：基准准确性与校准误差 (Ground-Truth Accuracy / ND-RMSE, 分数主干，不参与折扣)")
    md_lines.append(f"- **校准 ND 均方根误差 (Calibrated ND-RMSE)**: `{acc.global_rmse:.4f}`")
    md_lines.append(f"- **平均绝对误差 (Global MAE)**: `{acc.global_mae:.4f}`")
    md_lines.append(f"- **斯皮尔曼拓扑秩相关系数 (Spearman's $\\rho$)**: `{acc.spearman_rho:.4f}` ($p = {acc.spearman_p:.2e}$)")
    md_lines.append(f"- **肯德尔和谐系数 (Kendall's $\\tau$)**: `{acc.kendall_tau:.4f}`")
    md_lines.append(f"- **同级相对离散度 (Mean Within-Tier Dispersion, WTD)**: `{acc.mean_wtd:.4f}` *(反映同级等难谱面的内部凝聚度，越低越好)*")
    md_lines.append(
        f"- **子键型加权 RMSE (Sub-Pattern Weighted RMSE, 家族权重 × 族内 3:1)**: `{acc.weighted_stratified_rmse:.4f}`"
    )
    family_rmse = getattr(acc, "weighted_family_rmse", 0.0)
    if family_rmse:
        md_lines.append(
            f"- **键型加权 RMSE（诊断值，仅家族层）**: `{family_rmse:.4f}`"
        )
    macro_penalty = getattr(score, "macro_penalty", 0.0)
    if macro_penalty > 0:
        macro_severity = getattr(score, "macro_severity", 0.0)
        md_lines.append(
            f"- **倒挂惩罚 (Structural-Inversion Penalty, 已计入 ACC 分)**: **`−{macro_penalty:.2f}`** 分 "
            f"*(多尺度严重度 S = {macro_severity:.4f}；相位平均、k=2/3/5 粗粒化下沉量)*"
        )
    md_lines.append("")

    # Stratified table
    profile = profile_domains(
        acc.stratified_rmse,
        pattern_counts=getattr(acc, "stratified_counts", None),
    )
    verdict_by_pattern = {v.pattern: v for v in profile.verdicts}

    md_lines.append("#### 分键型准确性明细 (Pattern-Stratified Breakdown, 包含 Course)")
    md_lines.append("| 键型分类 (Pattern) | 样本数 (Samples) | 单键型 RMSE (ND) | 相对基线比值 | 领域判定 | 说明 |")
    md_lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
    for pat, p_rmse in acc.stratified_rmse.items():
        verdict = verdict_by_pattern.get(pat.lower())
        desc = describe_pattern(pat)
        if verdict is not None:
            n_str = str(verdict.samples) if verdict.samples >= 0 else "-"
            ratio_str = f"{verdict.ratio:.2f}x"
            verdict_str = f"**{verdict.label}**"
        else:
            n_str = "-"
            ratio_str = "-"
            verdict_str = "-"
        md_lines.append(
            f"| `{pat}` | {n_str} | **{p_rmse:.4f}** | {ratio_str} | {verdict_str} | {desc} |"
        )
    md_lines.append("")

    # Sub-pattern breakdown (binomial `family-subpattern` ids).  The weights are
    # the fixed within-family 3:1 split that drives the ACC RMSE sub-score.
    sub_map = getattr(acc, "stratified_subpattern_rmse", {}) or {}
    sub_counts = getattr(acc, "stratified_subpattern_counts", {}) or {}
    if sub_map:
        from cab.metrics.accuracy import (
            DEFAULT_PATTERN_WEIGHT,
            PATTERN_WEIGHTS,
            binomial_sub_pattern,
            sub_pattern_ratio,
        )

        md_lines.append("#### 子键型准确性明细 (Sub-Pattern Breakdown, 家族内 3:1 加权)")
        md_lines.append("")
        md_lines.append("| 子键型 (family-subpattern) | 样本数 | 单键型 RMSE (ND) | 族内权重 | 全局权重 |")
        md_lines.append("| :--- | :---: | :---: | :---: | :---: |")
        fam_order = sorted(
            sub_map, key=lambda f: -PATTERN_WEIGHTS.get(f, DEFAULT_PATTERN_WEIGHT)
        )
        for fam in fam_order:
            subs = sub_map.get(fam) or {}
            if not subs:
                continue
            ratios = {s: sub_pattern_ratio(s) for s in subs}
            total_ratio = sum(ratios.values()) or 1.0
            fam_w = PATTERN_WEIGHTS.get(fam, DEFAULT_PATTERN_WEIGHT)
            for sub, rmse in sorted(subs.items(), key=lambda kv: (-ratios[kv[0]], kv[0])):
                n = (sub_counts.get(fam) or {}).get(sub, 0)
                share = ratios[sub] / total_ratio
                md_lines.append(
                    f"| `{binomial_sub_pattern(fam, sub)}` | {n} | **{rmse:.4f}** | {share * 100:.1f}% | {fam_w * share * 100:.2f}% |"
                )
        md_lines.append("")

    # Programmatic domain profile (strengths / weaknesses)
    md_lines.extend(_render_domain_profile(profile, algo_name=result.algorithm_name))

    # Declared difficulty scope: a secondary in-scope reading. It never alters
    # any score above -- it only restates what the algorithm declared it does
    # not attempt, plus how well it does inside the slice it targets.
    scope = getattr(result, "scope_report", None)
    if scope is not None and scope.declarations:
        in_acc = getattr(result, "in_scope_accuracy", None)
        total_n = len(scope.in_scope_samples) + len(scope.out_of_scope_samples)
        md_lines.append("#### 声明难度域内二次读数 (Declared-Scope Reading)")
        md_lines.append(f"- **声明内容 (Declarations)**: `{scope.description}` *(经 `--declare-scope` 声明)*")
        md_lines.append(
            f"- **域内样本 (In-Scope Charts)**: `{len(scope.in_scope_samples)}` / `{total_n}` 首"
            f" *(域外 {len(scope.out_of_scope_samples)} 首)*"
        )
        md_lines.append(
            f"- **让渡的真实用量占比 (Real-World Usage Given Up)**: `{scope.out_of_scope_usage * 100:.1f}%`"
            " *(据公开分析器近 90 天使用分布估算，仅供解读)*"
        )
        if in_acc is not None:
            md_lines.append(
                f"- **域内二次校准 ND-RMSE (In-Scope ND-RMSE)**: `{in_acc.global_rmse:.4f}`"
                f" | **MAE**: `{in_acc.global_mae:.4f}`"
                f" | **Spearman's $\\rho$**: `{in_acc.spearman_rho:.4f}`"
            )
            md_lines.append(f"- **域内同级相对离散度 (In-Scope WTD)**: `{in_acc.mean_wtd:.4f}`\n")
        else:
            md_lines.append("")
            md_lines.append("> [!NOTE]")
            md_lines.append("> 声明后域内样本不足两首，二次读数未计算。\n")
        md_lines.append("> **注**: 声明不改变上方任何正式评分与六个维度，完整口径见使用指南 §7.3。\n")

    # Macro Inversion Alarm
    if acc.calibration.macro_alerts:
        md_lines.append("> [!WARNING]")
        md_lines.append(f"> **检测到 {len(acc.calibration.macro_alerts)} 处宏观层级倒吸违规 (Macro Inversion Alarm)**：")
        for alert in acc.calibration.macro_alerts:
            md_lines.append(
                f"> - `ND {alert.lower_tier:.1f}` (质心 {alert.lower_anchor:.4f}) "
                f"> `ND {alert.higher_tier:.1f}` (质心 {alert.higher_anchor:.4f}) "
                f"| 倒吸幅度: **+{alert.violation_amount:.4f}**"
            )
        md_lines.append("")

    if "calibration" in images:
        md_lines.append(f"![PAVA Calibration Curve]({images['calibration']})\n")

    # 维度二
    if inv is not None:
        md_lines.append(f"### 2.2 维度二：抗倒吸测试 (Anti-Rate-Inversion Monotonicity, 折扣 β {BETA_INVERSION*100:g}%)")
        md_lines.append(f"- **倒吸频率 (Inversion Frequency, IF)**: `{inv.inversion_frequency*100:.3f}%`")
        md_lines.append(f"- **倒吸曲面积分亏损 (Inversion Deficit Area, IDA)**: `{inv.auc_deficit:.6f}`")
        md_lines.append(f"- **最大相对倒吸跌幅 (Max Relative Drop)**: `{inv.max_relative_drop*100:.3f}%`")
        md_lines.append(f"- **最大绝对难度下跌 (Max Absolute Drop)**: `{inv.max_absolute_drop:.4f}`")
        md_lines.append(f"- **发生倒吸的谱面数**: `{inv.inverted_maps_count}` / `{inv.total_maps_evaluated}` 首")
        md_lines.append(f"- **综合倒吸指数 (Composite Inversion Index)**: `{inv.composite_inversion_index:.4f}`\n")
        if "inversion" in images:
            md_lines.append(f"![Rate Inversion Profiles]({images['inversion']})\n")

    # 维度三
    md_lines.append(f"### 2.3 维度三：抗短面测试 (Anti-Short LN / Release-Tap Artifact, 折扣 β {BETA_SHORT_LN*100:g}%)")
    md_lines.append(f"- **短面自基准 RMSE (Short-LN RMSE)**: `{sln.short_ln_rmse:.6f}`")
    md_lines.append(f"- **虚高膨胀倾向比 (Inflation Tendency Ratio, ITR)**: `{sln.inflation_tendency_ratio:+.4f}%`")
    md_lines.append(f"- **最大难度漂移 (Max Absolute Delta)**: `{sln.max_absolute_delta:.6f}`")
    if getattr(sln, "ho_delta_pct", None) is not None:
        md_lines.append(f"- **全长条剥离敏感度探针 (HO Probe Delta)**: `{sln.ho_delta_pct:.2f}%`")
    md_lines.append(f"- **零响应判定 (Zero-Response Probe)**: `{'是 (输出完全未变化)' if sln.has_native_immunity else '否 (输出有变化)'}`")
    md_lines.append(f"- **双侧容差带与门控评级 (Band + HO Gate Grade)**: **`{sln.immunity_grade}`**")
    md_lines.append(
        f"- **抗短面得分 (ASLN)**: **`{sln.score:.2f}`** / 100 "
        f"*(目标 ITR 7%，耦合连续 HO 长条能力门控)*\n"
    )

    # 维度四
    if cont is not None:
        md_lines.append(f"### 2.4 维度四：局部连续性与断崖测试 (Local Continuity & Cliffing, 折扣 β {BETA_CONTINUITY*100:g}%)")
        md_lines.append(
            f"- **断崖事件 (Cliff Events)**: `{cont.cliff_events_count}` 次 "
            f"(倍速跨度占比 `{getattr(cont, 'cliff_rate', 0.0)*100:.2f}%`)"
        )
        md_lines.append(f"- **归一化斜率粗糙度 (Normalized Slope Roughness)**: `{getattr(cont, 'mean_roughness', 0.0):.4f}`")
        md_lines.append(
            f"- **最大局部利普希茨梯度 (Max Lipschitz $\\Lambda$)**: `{cont.max_lipschitz:.2f}` "
            f"(无量纲归一化 `{getattr(cont, 'max_normalized_lipschitz', 0.0):.2f}`)"
        )
        md_lines.append(f"- **平均局部利普希茨梯度 (Mean Lipschitz)**: `{cont.mean_lipschitz:.2f}`")
        md_lines.append(f"- **平滑度得分 (Smoothness Score)**: `{cont.smoothness_score:.2f}` / 100\n")

    # 维度五
    if spd is not None:
        md_lines.append(f"### 2.5 维度五：算法计算开销与计算速度 (Computation Speed, 折扣 β {BETA_SPEED*100:g}%)")
        md_lines.append(f"- **平均计算速度 (Average Speed)**: **`{spd.average_speed_ms_per_s:.4f} ms/s`** *(每 1 秒谱面时长消耗的计算毫秒数)*")
        md_lines.append(f"- **中位数计算速度 (Median Speed)**: `{spd.median_speed_ms_per_s:.4f} ms/s`")
        md_lines.append(f"- **P95 计算速度 (P95 Speed)**: `{spd.p95_speed_ms_per_s:.4f} ms/s` *(95% 的样本次于该速度；游戏场景的“最坏帧”参考)*")
        md_lines.append(f"- **最慢样本速度 (Max Speed)**: `{spd.max_speed_ms_per_s:.4f} ms/s` *(单张谱面最慢计算速度)*")
        md_lines.append(f"- **实时倍率当量 (Realtime Factor)**: **`{spd.realtime_factor:.1f}x`** *(相当于实时音乐播放速率的倍数)*")
        md_lines.append(f"- **计算速度得分**: **`{spd.speed_score:.1f}`** / 100")
        md_lines.append(f"- **评测样本谱面数**: `{spd.evaluated_count}` 首\n")

    # 维度六
    md_lines.append(f"### 2.6 维度六：抗抖动时间微扰测试 (Anti-Jitter Robustness, 折扣 β {BETA_JITTER*100:g}%)")
    md_lines.append(f"- **抖动自基准 RMSE (Jitter Self-RMSE)**: `{jit.jitter_rmse:.6f}`")
    md_lines.append(f"- **平均绝对百分比误差 (MAPE, OD自适应微扰)**: `{jit.mape:.4f}%`")
    md_lines.append(f"- **最大绝对扰动漂移 (Max Absolute Delta)**: `{jit.max_absolute_delta:.6f}`")
    md_lines.append(f"- **最大相对扰动漂移 (Max Relative Delta)**: `{jit.max_relative_delta*100:.3f}%`")
    if getattr(jit, "arrangement_sensitivity_pct", None) is not None:
        md_lines.append(
            f"- **同密度排列畸变探针响应 (Arrangement Probe Delta)**: `{jit.arrangement_sensitivity_pct:.2f}%` "
            f"*({'检测到窗口密度盲视 (<3%)' if getattr(jit, 'is_arrangement_blind', False) else '具备正常局部排列敏感度'})*"
        )
    md_lines.append(f"- **抗抖动得分 (AJ)**: **`{jit.score:.2f}`** / 100\n")

    # 结论与诊断
    md_lines.append("## 3. 算法工程诊断与优化罗盘 (Engineering Diagnosis & Recommendations)")
    md_lines.append("")
    if sln.has_native_immunity or getattr(sln, "is_brute_force_tap", False):
        md_lines.append("> [!WARNING]")
        md_lines.append(
            f"> **长条盲视 / 短面零响应**: `{result.algorithm_name}` 对长条剥离（HO 探针）或 20–100ms 短面注入缺乏真实响应。"
            "说明其底层未对长音符释放做有效建模，连续 HO 能力门控已按真实长条敏感度予以扣减。"
        )
        md_lines.append("")

    if inv is not None and inv.inversion_frequency > 0.05:
        md_lines.append("> [!WARNING]")
        md_lines.append(
            f"> **倍速单调性存在倒吸**: 当前倍速倒吸频率达 `{inv.inversion_frequency*100:.1f}%`。"
            " 建议检查物理密度积分中的非线性衰减项或分段阈值切分，防止高加速状态下的耐力/密度特征出现负梯度。"
        )
        md_lines.append("")

    if acc.spearman_rho >= 0.85:
        md_lines.append("> [!NOTE]")
        md_lines.append(
            f"> **拓扑排位准确度高**: 斯皮尔曼秩相关达 `{acc.spearman_rho:.4f}`，"
            "说明算法对谱面客观难度的相对排序极其准确，具备优秀的段位排位指导价值。"
        )
        md_lines.append("")

    failed_bm = getattr(result, "failed_beatmaps", [])
    if failed_bm:
        md_lines.append("## 4. 谱面异常与自动排除记录 (Excluded Failed Beatmaps)")
        md_lines.append("> [!WARNING]")
        md_lines.append(f"> 测试过程中检测到 **{len(failed_bm)}** 首谱面在算法计算时发生异常报错或崩溃。基准已按容错规则将其自动剔除，未计入评分：\n")
        md_lines.append("| 谱面文件名称 | 标定参考难度 | 键型分类 | 异常报错信息摘要 |")
        md_lines.append("| :--- | :---: | :---: | :--- |")
        for fb in failed_bm:
            md_lines.append(f"| `{fb['name']}` | `{fb['expected']}` | `{fb['pattern']}` | `{fb['error']}` |")
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("*Report generated automatically by CAB-VA (Consistency and Accuracy Bench for VSRG Algorithm).*")

    content = "\n".join(md_lines)
    with open(out_file, "w", encoding="utf-8") as fp:
        fp.write(content)

    return out_file


def generate_multi_tier_summary_report(
    multi_res: MultiTierBenchmarkResult,
    output_path: str | Path,
    image_paths: Optional[dict[str, str]] = None,
) -> Path:
    """Generate master multi-tier comparison markdown report."""
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    images = image_paths or {}

    md_lines: list[str] = []
    md_lines.append(f"# CAB-VA 多档位综合评测总报告: {multi_res.algorithm_name}")
    md_lines.append(f"**Consistency and Accuracy Bench for VSRG Algorithm (CAB-VA) Multi-Tier Suite**\n")
    md_lines.append(f"> **待测算法**: `{multi_res.algorithm_name}`  ")
    md_lines.append(f"> **标尺类型**: `{multi_res.scale_type}`  ")
    md_lines.append(f"> **测试模式**: `{multi_res.config.mode.upper()}` (步长: $\\Delta r = {multi_res.config.rate_step}$)  ")
    env = collect_environment_info()
    md_lines.append(f"> **评测环境**: `{env['cpu_model']} · {env['cpu_cores']} 核 · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(速度类指标仅同环境直接可比)*  ")
    md_lines.append(f"> **评测总耗时**: `{multi_res.duration_seconds:.2f}` 秒  ")
    md_lines.append(f"> **多档位架构**: 零冗余单次执行四档切片 (Zero-Redundant 4-Tier Slicing)  ")
    scope_report = getattr(multi_res.master_result, "scope_report", None)
    if scope_report is not None and scope_report.declarations:
        in_acc = getattr(multi_res.master_result, "in_scope_accuracy", None)
        kept = len(scope_report.in_scope_samples)
        total = kept + len(scope_report.out_of_scope_samples)
        rmse_bit = f"；域内二次 ND-RMSE `{in_acc.global_rmse:.4f}`" if in_acc is not None else ""
        md_lines.append(
            f"> **声明难度域**: `{scope_report.description}`（域内 {kept}/{total} 首，让渡真实用量 {scope_report.out_of_scope_usage * 100:.1f}%{rmse_bit}）  "
        )
    md_lines.append("")

    # Section 1: Executive 4-Tier Comparison Table
    md_lines.append("## 1. 四大难度档位横向对比总表 (Executive Multi-Tier Scorecard)")
    md_lines.append("")
    md_lines.append("| 评测档位 (Tier) | 难度区间 (Range) | 样本数 (Maps) | CAB-Score | 准确性 (66.67%*) | 抗倒吸 (15%*) | 抗短面 (20%*) | 局部连续 (10%*) | 计算速度 (6.67%*) | 抗抖动 (6.67%*) | Calib RMSE |")
    md_lines.append("| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |")

    tier_order = [TIER_ALL, TIER_NUMBER, TIER_GREEK, TIER_INHUMAN]
    for t_id in tier_order:
        if t_id not in multi_res.tier_results:
            continue
        tr = multi_res.tier_results[t_id]
        if t_id == TIER_ALL:
            r_str = "$\\ge -2.5$"
        elif t_id == TIER_NUMBER:
            r_str = "$[-2.5, 10.5)$"
        elif t_id == TIER_GREEK:
            r_str = "$[10.5, 15.5]$"
        else:
            r_str = "$> 15.5$"

        n_maps = len(tr.accuracy.calibrated_predictions)
        s = tr.score
        if s.is_out_of_domain:
            md_lines.append(
                f"| **{tr.tier_name}** | {r_str} | {n_maps} | **`N/A`** | "
                f"N/A | - | N/A | - | N/A | N/A | `N/A` |"
            )
        else:
            s_inv_str = f"{s.s_inversion:.1f}" if s.s_inversion is not None else "-"
            s_cont_str = f"{s.s_continuity:.1f}" if s.s_continuity is not None else "-"
            rmse_str = f"`{tr.accuracy.global_rmse:.3f}`" if n_maps > 0 else "`N/A`"
            md_lines.append(
                f"| **{tr.tier_name}** | {r_str} | {n_maps} | **`{s.total_score:.1f}`** / 100 | "
                f"{s.s_accuracy:.1f} | {s_inv_str} | {s.s_short_ln:.1f} | {s_cont_str} | "
                f"{s.s_speed:.1f} | {s.s_jitter:.1f} | {rmse_str} |"
            )
    md_lines.append("")
    md_lines.append("> **注**: 子档位 (数字段/字母段/非人类段) 仅评估静态 4 维度 (ACC 66.67%, ASLN 20%, SPD 6.67%, AJ 6.67%)；动态变速指标 (抗倒吸 AIN 15% 与 局部连续 CT 10%) 仅在全域段 (All Range) 统一评定。")
    md_lines.append("")

    if "multi_tier_comparison" in images:
        md_lines.append(f"![CAB Multi-Tier Comparison]({images['multi_tier_comparison']})\n")

    # Section 2: Detailed Tier Breakdown
    md_lines.append("## 2. 各难度档位技术明细与表现特征 (Tier-by-Tier Technical Analysis)")
    md_lines.append("")
    tier_descriptions = {
        TIER_ALL: "反映算法在全域难度下的整体鲁棒性、通用性与跨阶标定能力。",
        TIER_NUMBER: "覆盖初学者到进阶段位 (Intro 1~3 至 10th Dan)，核心考察低速低密度渐进敏感度、音符密集度感知与初中级平滑度。",
        TIER_GREEK: "覆盖 Alpha 至 Epsilon ([10.5, 15.5])，属于竞技排位与段位考试核心区域。专攻高难段位的特化算法在此区间具备核心设计标定。",
        TIER_INHUMAN: "覆盖 Zeta (16.0) 至 Theta/Kappa (18+) 及以上超极限谱面，核心检验物理密度积分在超高速下的渐近发散度与稳定性。",
    }

    for t_id in tier_order:
        if t_id not in multi_res.tier_results:
            continue
        tr = multi_res.tier_results[t_id]
        md_lines.append(f"### 2.{tier_order.index(t_id)+1} {tr.tier_name}")
        md_lines.append(f"> **定位说明**: {tier_descriptions.get(t_id, '')}\n")
        if tr.score.is_out_of_domain:
            md_lines.append("> [!NOTE]\n> **状态**: 该档位无可用有效谱面样本，超出算法当前指定的计算域 (`N/A`)。\n")
            continue
        md_lines.append(f"- **综合得分**: **`{tr.score.total_score:.1f}` / 100**")
        md_lines.append(f"- **校准 ND-RMSE**: `{tr.accuracy.global_rmse:.4f}` | **秩相关系数 (Spearman's $\\rho$)**: `{tr.accuracy.spearman_rho:.4f}`")
        wtd_str = f"{tr.accuracy.mean_wtd:.4f}"
        if tr.inversion is not None:
            inv_str = f"`{tr.inversion.inversion_frequency*100:.2f}%`"
            md_lines.append(f"- **同级相对离散度 (WTD)**: `{wtd_str}` | **倒吸频率 (IF)**: {inv_str}")
        else:
            md_lines.append(f"- **同级相对离散度 (WTD)**: `{wtd_str}` | **抗倒吸 (AIN)**: `-` *(仅全域段评估)*")

        if tr.continuity is not None:
            cont_str = f"`{tr.continuity.cliff_events_count}` 次"
            md_lines.append(f"- **抗短面得分**: `{tr.score.s_short_ln:.1f}`/100 (评级: `{tr.short_ln.immunity_grade}`) | **断崖事件**: {cont_str}")
        else:
            md_lines.append(f"- **抗短面得分**: `{tr.score.s_short_ln:.1f}`/100 (评级: `{tr.short_ln.immunity_grade}`) | **局部连续 (CT)**: `-` *(仅全域段评估)*")

        md_lines.append(f"- **计算速度**: `{tr.speed.average_speed_ms_per_s:.4f} ms/s` ({tr.speed.realtime_factor:.1f}x 实时当量) | **抗抖动得分**: `{tr.score.s_jitter:.1f}`/100\n")

        # Per-tier programmatic domain profile
        tier_profile = profile_domains(
            tr.accuracy.stratified_rmse,
            pattern_counts=getattr(tr.accuracy, "stratified_counts", None),
        )
        md_lines.extend(_render_domain_profile(tier_profile, algo_name=multi_res.algorithm_name))

    # Section 3: Engineering Insights
    md_lines.append("## 3. 算法特化领域诊断与研发罗盘 (Engineering Diagnosis & Domain Fitness)")
    md_lines.append("")
    all_tr = multi_res.tier_results.get(TIER_ALL)
    num_tr = multi_res.tier_results.get(TIER_NUMBER)
    grk_tr = multi_res.tier_results.get(TIER_GREEK)
    inh_tr = multi_res.tier_results.get(TIER_INHUMAN)

    if grk_tr and num_tr and not num_tr.score.is_out_of_domain and not grk_tr.score.is_out_of_domain:
        if grk_tr.score.total_score > num_tr.score.total_score + 8.0:
            md_lines.append("> [!TIP]")
            md_lines.append(
                f"> **高难段位特化算法特征**: `{multi_res.algorithm_name}` 在字母段 (Greek Dan) 得分 (`{grk_tr.score.total_score:.1f}`) "
                f"显著高于数字段 (`{num_tr.score.total_score:.1f}`)。该算法极其适合高难竞技考试与核心段位评价，而在中低难度段有一定低估或饱和现象。"
            )
            md_lines.append("")
        elif abs(grk_tr.score.total_score - num_tr.score.total_score) <= 5.0:
            md_lines.append("> [!NOTE]")
            md_lines.append(
                f"> **全域均衡型算法特征**: `{multi_res.algorithm_name}` 在数字段 (`{num_tr.score.total_score:.1f}`) 与字母段 (`{grk_tr.score.total_score:.1f}`) "
                f"表现出高度一致的评分水准，具备出色的全谱面通用性与平滑线性映射能力。"
            )
            md_lines.append("")

    if inh_tr and not inh_tr.score.is_out_of_domain and inh_tr.accuracy.global_rmse > 1.8:
        md_lines.append("> [!WARNING]")
        md_lines.append(
            f"> **非人类段外推发散预警**: 在非人类段 (Inhuman Dan, > 15.5)，校准误差增大至 `{inh_tr.accuracy.global_rmse:.3f}` ND。"
            " 建议优化超极限音符密度下的局部饱和函数 (如对极高 NPS 增加对数或双曲正切软上限)。"
        )
        md_lines.append("")

    failed_bm = getattr(multi_res, "failed_beatmaps", [])
    if failed_bm:
        md_lines.append("## 4. 谱面异常与自动排除记录 (Excluded Failed Beatmaps)")
        md_lines.append("> [!WARNING]")
        md_lines.append(f"> 测试过程中检测到 **{len(failed_bm)}** 首谱面在算法计算时发生异常报错或崩溃。基准已按容错规则将其自动剔除，未计入评分：\n")
        md_lines.append("| 谱面文件名称 | 标定参考难度 | 键型分类 | 异常报错信息摘要 |")
        md_lines.append("| :--- | :---: | :---: | :--- |")
        for fb in failed_bm:
            md_lines.append(f"| `{fb['name']}` | `{fb['expected']}` | `{fb['pattern']}` | `{fb['error']}` |")
        md_lines.append("")

    md_lines.append("---")
    md_lines.append("*Report generated automatically by CAB-VA Multi-Tier Engine.*")

    content = "\n".join(md_lines)
    with open(out_file, "w", encoding="utf-8") as fp:
        fp.write(content)

    return out_file

