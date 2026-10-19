"""Standardized Markdown Benchmark Report Generator for CAB-VA (6 Dimensions, 100-Point Scale).

Every human-readable string is written as an inline ``t(zh, en)`` pair and
``lang`` decides which one is rendered, so a run emits an identical report in
both Chinese and English. The numeric payload is language-neutral and shared.
"""

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
    PatternVerdict,
    describe_pattern,
    describe_pattern_en,
    format_pattern_list,
    profile_domains,
)
from cab.runner.suite import BenchmarkRunResult, MultiTierBenchmarkResult

SUPPORTED_LANGS = ("zh", "en")


def _pick(zh: str, en: str, lang: str) -> str:
    return en if lang == "en" else zh


def _pattern_list(items: list[PatternVerdict], lang: str) -> str:
    """`pattern (ratio)` list; the zh version keeps the localized pattern names."""
    if not items:
        return "—"
    if lang == "en":
        return ", ".join(f"`{v.pattern}` ({v.ratio:.2f}x)" for v in items)
    return format_pattern_list(items)


def _pattern_names(items: list[PatternVerdict], lang: str) -> str:
    if not items:
        return "—"
    if lang == "en":
        return ", ".join(v.pattern for v in items)
    return "，".join(v.described_pattern for v in items)


def _render_domain_profile(profile: DomainProfile, algo_name: str, lang: str = "zh") -> list[str]:
    """Render the programmatically-derived strengths / weaknesses section."""
    t = lambda zh, en: _pick(zh, en, lang)  # noqa: E731
    lines: list[str] = []
    lines.append(t("#### 算法领域适配画像 (Domain Fitness Profile, 程序化判定)",
                   "#### Domain fitness profile (programmatic verdict)"))
    lines.append("")
    if not profile.has_judged:
        lines.append(
            t("> [!NOTE]\n"
              "> **无法判定**: 当前样本中没有任何键型达到最小样本量阈值 "
              f"(`>= {profile.min_samples}` 首)，不足以对该算法的擅长/不擅长领域做出可靠结论。\n",
              "> [!NOTE]\n"
              "> **Cannot judge**: no pattern in this sample reaches the minimum bucket size "
              f"(`>= {profile.min_samples}` charts), so no reliable strength/weakness verdict can be made.\n")
        )
        return lines

    lines.append(
        t(f"**判定基准**: 以自身各键型 RMSE 的中位数 `{profile.baseline_rmse:.4f}`（ND）作为内部基线，"
          "`比值 = 该键型 RMSE / 基线`；"
          "`<= 0.80` 判为**擅长**，`0.80 ~ 1.20` 判为**达标**，`> 1.20` 判为**不擅长**；"
          f"样本量 `< {profile.min_samples}` 首的键型不参与判定 (标注为样本不足)。\n",
          f"**Baseline**: the median of this algorithm's own per-pattern RMSE, `{profile.baseline_rmse:.4f}` (ND); "
          "`ratio = pattern RMSE / baseline`. "
          "`<= 0.80` counts as a **strength**, `0.80 ~ 1.20` as **adequate**, `> 1.20` as a **weakness**; "
          f"patterns with fewer than `{profile.min_samples}` charts are not judged (marked under-sampled).\n")
    )
    lines.append(t("| 判定结论 | 覆盖键型 (相对基线比值) |", "| Verdict | Patterns (ratio vs baseline) |"))
    lines.append("| :--- | :--- |")
    lines.append(t(f"| 擅长 (Strength) | {_pattern_list(profile.strengths, lang)} |",
                   f"| Strength | {_pattern_list(profile.strengths, lang)} |"))
    lines.append(t(f"| 达标 (Adequate) | {_pattern_list(profile.adequate, lang)} |",
                   f"| Adequate | {_pattern_list(profile.adequate, lang)} |"))
    lines.append(t(f"| 不擅长 (Weakness) | {_pattern_list(profile.weaknesses, lang)} |",
                   f"| Weakness | {_pattern_list(profile.weaknesses, lang)} |"))
    if profile.insufficient:
        lines.append(t(f"| 样本不足 (Under-sampled) | {_pattern_list(profile.insufficient, lang)} |",
                       f"| Under-sampled | {_pattern_list(profile.insufficient, lang)} |"))
    lines.append("")

    if profile.strengths:
        lines.append("> [!TIP]")
        lines.append(
            t(f"> **擅长领域**: `{algo_name}` 在 **{_pattern_names(profile.strengths, lang)}** 上的判定误差显著低于其自身平均水准"
              f"（分别 {_pattern_list(profile.strengths, lang)}），说明算法在这些键型的物理建模上具备优势。",
              f"> **Strengths**: `{algo_name}` is markedly better than its own average on "
              f"**{_pattern_names(profile.strengths, lang)}** ({_pattern_list(profile.strengths, lang)}), "
              "which points to solid physical modelling for those patterns.")
        )
        lines.append("")

    if profile.weaknesses:
        lines.append("> [!WARNING]")
        lines.append(
            t(f"> **不擅长领域**: `{algo_name}` 在 **{_pattern_names(profile.weaknesses, lang)}** 上的判定误差明显高于其自身平均水准"
              f"（分别 {_pattern_list(profile.weaknesses, lang)}）。建议优先针对这些键型检查特征提取与密度积分项。",
              f"> **Weaknesses**: `{algo_name}` is clearly worse than its own average on "
              f"**{_pattern_names(profile.weaknesses, lang)}** ({_pattern_list(profile.weaknesses, lang)}). "
              "Worth checking feature extraction and the density integral for those patterns first.")
        )
        lines.append("")

    return lines


def generate_markdown_report(
    result: BenchmarkRunResult,
    output_path: str | Path,
    image_paths: Optional[dict[str, str]] = None,
    lang: str = "zh",
) -> Path:
    """Generate comprehensive GitHub-flavored Markdown report."""
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"unsupported lang: {lang!r}")
    t = lambda zh, en: _pick(zh, en, lang)  # noqa: E731
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
    md_lines.append(t(f"# CAB-VA 基准测试报告: {result.algorithm_name}",
                      f"# CAB-VA benchmark report: {result.algorithm_name}"))
    md_lines.append(f"**Consistency and Accuracy Bench for VSRG Algorithm (CAB-VA)**\n")
    md_lines.append(t(f"> **待测算法**: `{result.algorithm_name}`  ",
                      f"> **Algorithm**: `{result.algorithm_name}`  "))
    md_lines.append(t(f"> **标尺类型**: `{result.scale_type}`  ", f"> **Scale type**: `{result.scale_type}`  "))
    tier_id = getattr(result, "tier_id", "all")
    tier_name_zh = getattr(result, "tier_name", "全域段 (All Range)")
    md_lines.append(
        t(f"> **测试档位**: `{tier_name_zh}` (`{tier_id}`)  ",
          f"> **Tier**: `{_TIER_NAME_EN.get(tier_id, tier_name_zh)}` (`{tier_id}`)  ")
    )
    md_lines.append(t(f"> **测试模式**: `{result.config.mode.upper()}` (步长: $\\Delta r = {result.config.rate_step}$)  ",
                      f"> **Mode**: `{result.config.mode.upper()}` (step: $\\Delta r = {result.config.rate_step}$)  "))
    if result.config.has_filters():
        md_lines.append(t(f"> **数据集过滤**: `{result.filter_summary}`  ",
                          f"> **Dataset filter**: `{result.filter_summary}`  "))
        if result.sample_stats:
            md_lines.append(
                t(f"> **过滤后样本数**: `{result.sample_stats.get('total', len(acc.calibrated_predictions))}` 首  ",
                  f"> **Charts after filtering**: `{result.sample_stats.get('total', len(acc.calibrated_predictions))}`  ")
            )
    env = collect_environment_info()
    md_lines.append(
        t(f"> **评测环境**: `{env['cpu_model']} · {env['cpu_cores']} 核 · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(速度类指标仅同环境直接可比)*  ",
          f"> **Environment**: `{env['cpu_model']} · {env['cpu_cores']} cores · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(speed metrics are only comparable within one environment)*  ")
    )
    md_lines.append(t(f"> **评估耗时**: `{result.duration_seconds:.2f}` 秒  \n",
                      f"> **Wall time**: `{result.duration_seconds:.2f}` s  \n"))

    # Score Card
    md_lines.append(t("## 1. 综合评测总览与评分卡 (Executive Scorecard)", "## 1. Executive scorecard"))
    md_lines.append("")
    spd_score_str = f"{score.s_speed:.1f}" if spd is not None else "N/A"
    if score.is_subtier:
        md_lines.append(
            t(f"| 标准化 CAB-Score (总分) | 准确性 (主干) | 抗短面 (β {BETA_SHORT_LN*100:g}%) | 计算速度 (β {BETA_SPEED*100:g}%) | 抗抖动 (β {BETA_JITTER*100:g}%) |",
              f"| CAB Score | Accuracy (backbone) | Anti-Short-LN (β {BETA_SHORT_LN*100:g}%) | Speed (β {BETA_SPEED*100:g}%) | Anti-Jitter (β {BETA_JITTER*100:g}%) |")
        )
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
        md_lines.append(
            t("> **注**: 当前档位为子难度档位。总分 = ACC × Π[1 − β·(1 − s/100)]，仅对已评测的 ASLN / SPD / AJ 施加折扣；"
              "动态倍速维度（抗倒吸 / 局部连续）不在此档位评测，其折扣直接不参与乘积（无需重新归一化）。",
              "> **Note**: this is a sub-tier. Total = ACC x Π[1 - β·(1 - s/100)], with discounts applied only to the "
              "dimensions scored here (ASLN / SPD / AJ). The dynamic rate dimensions (Anti-Inversion / Continuity) are "
              "not evaluated in a sub-tier, so their discounts simply drop out of the product (no renormalisation).")
        )
    else:
        md_lines.append(
            t(f"| CAB 总分 | 准确性 (主干) | 抗倒吸 (β {BETA_INVERSION*100:g}%) | 抗短面 (β {BETA_SHORT_LN*100:g}%) | 局部连续 (β {BETA_CONTINUITY*100:g}%) | 计算速度 (β {BETA_SPEED*100:g}%) | 抗抖动 (β {BETA_JITTER*100:g}%) |",
              f"| CAB Score | Accuracy (backbone) | Anti-Inversion (β {BETA_INVERSION*100:g}%) | Anti-Short-LN (β {BETA_SHORT_LN*100:g}%) | Continuity (β {BETA_CONTINUITY*100:g}%) | Speed (β {BETA_SPEED*100:g}%) | Anti-Jitter (β {BETA_JITTER*100:g}%) |")
        )
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

    # Six dimensions
    md_lines.append(t("## 2. 评测技术明细 (Detailed Dimension Results)", "## 2. Detailed dimension results"))
    md_lines.append("")

    # Dimension 1
    md_lines.append(
        t("### 2.1 维度一：基准准确性与校准误差 (Ground-Truth Accuracy / ND-RMSE, 分数主干，不参与折扣)",
          "### 2.1 Dimension 1: ground-truth accuracy and calibration error (ND-RMSE, backbone, never discounted)")
    )
    md_lines.append(t(f"- **校准 ND 均方根误差 (Calibrated ND-RMSE)**: `{acc.global_rmse:.4f}`",
                      f"- **Calibrated ND-RMSE**: `{acc.global_rmse:.4f}`"))
    md_lines.append(t(f"- **平均绝对误差 (Global MAE)**: `{acc.global_mae:.4f}`",
                      f"- **Global MAE**: `{acc.global_mae:.4f}`"))
    md_lines.append(
        t(f"- **斯皮尔曼拓扑秩相关系数 (Spearman's $\\rho$)**: `{acc.spearman_rho:.4f}` ($p = {acc.spearman_p:.2e}$)",
          f"- **Spearman's $\\rho$**: `{acc.spearman_rho:.4f}` ($p = {acc.spearman_p:.2e}$)")
    )
    md_lines.append(t(f"- **肯德尔和谐系数 (Kendall's $\\tau$)**: `{acc.kendall_tau:.4f}`",
                      f"- **Kendall's $\\tau$**: `{acc.kendall_tau:.4f}`"))
    md_lines.append(
        t(f"- **同级相对离散度 (Mean Within-Tier Dispersion, WTD)**: `{acc.mean_wtd:.4f}` *(反映同级等难谱面的内部凝聚度，越低越好)*",
          f"- **Mean within-tier dispersion (WTD)**: `{acc.mean_wtd:.4f}` *(how tightly same-tier charts cluster; lower is better)*")
    )
    md_lines.append(
        t(f"- **子键型加权 RMSE (Sub-Pattern Weighted RMSE, 键型权重 × 键型内 3:1)**: `{acc.weighted_stratified_rmse:.4f}`",
          f"- **Sub-pattern weighted RMSE (pattern weight x within-pattern 3:1)**: `{acc.weighted_stratified_rmse:.4f}`")
    )
    family_rmse = getattr(acc, "weighted_family_rmse", 0.0)
    if family_rmse:
        md_lines.append(
            t(f"- **键型加权 RMSE（诊断值，仅键型层）**: `{family_rmse:.4f}`",
              f"- **Pattern-weighted RMSE (diagnostic, pattern level only)**: `{family_rmse:.4f}`")
        )
    macro_penalty = getattr(score, "macro_penalty", 0.0)
    if macro_penalty > 0:
        macro_severity = getattr(score, "macro_severity", 0.0)
        md_lines.append(
            t(f"- **倒挂惩罚 (Structural-Inversion Penalty, 已计入 ACC 分)**: **`−{macro_penalty:.2f}`** 分 "
              f"*(多尺度严重度 S = {macro_severity:.4f}；相位平均、k=2/3/5 粗粒化下沉量)*",
              f"- **Structural-inversion penalty (already inside ACC)**: **`-{macro_penalty:.2f}`** pts "
              f"*(multi-scale severity S = {macro_severity:.4f}; phase-averaged dip at k = 2/3/5)*")
        )
    md_lines.append("")

    # Stratified table
    profile = profile_domains(
        acc.stratified_rmse,
        pattern_counts=getattr(acc, "stratified_counts", None),
    )
    verdict_by_pattern = {v.pattern: v for v in profile.verdicts}

    md_lines.append(t("#### 分键型准确性明细 (Pattern-Stratified Breakdown, 包含 Course)",
                      "#### Pattern-stratified accuracy (course included)"))
    md_lines.append(
        t("| 键型分类 (Pattern) | 样本数 (Samples) | 单键型 RMSE (ND) | 相对基线比值 | 领域判定 | 说明 |",
          "| Pattern | Samples | Per-pattern RMSE (ND) | Ratio vs baseline | Verdict | Notes |")
    )
    md_lines.append("| :--- | :---: | :---: | :---: | :---: | :--- |")
    for pat, p_rmse in acc.stratified_rmse.items():
        verdict = verdict_by_pattern.get(pat.lower())
        desc = describe_pattern_en(pat) if lang == "en" else describe_pattern(pat)
        if verdict is not None:
            n_str = str(verdict.samples) if verdict.samples >= 0 else "-"
            ratio_str = f"{verdict.ratio:.2f}x"
            verdict_str = t(f"**{verdict.label}**", f"**{verdict.label_en}**")
        else:
            n_str = "-"
            ratio_str = "-"
            verdict_str = "-"
        md_lines.append(
            f"| `{pat}` | {n_str} | **{p_rmse:.4f}** | {ratio_str} | {verdict_str} | {desc} |"
        )
    md_lines.append("")

    # Sub-pattern breakdown (binomial `pattern-subpattern` ids).  The weights are
    # the fixed within-pattern 3:1 split that drives the ACC RMSE sub-score.
    sub_map = getattr(acc, "stratified_subpattern_rmse", {}) or {}
    sub_counts = getattr(acc, "stratified_subpattern_counts", {}) or {}
    if sub_map:
        from cab.metrics.accuracy import (
            DEFAULT_PATTERN_WEIGHT,
            PATTERN_WEIGHTS,
            binomial_sub_pattern,
            sub_pattern_ratio,
        )

        md_lines.append(t("#### 子键型准确性明细 (Sub-Pattern Breakdown, 键型内 3:1 加权)",
                          "#### Sub-pattern breakdown (within-pattern 3:1 weighting)"))
        md_lines.append("")
        md_lines.append(
            t("| 子键型 (pattern-subpattern) | 样本数 | 单键型 RMSE (ND) | 键型内权重 | 全局权重 |",
              "| Sub-pattern | Samples | Per-bucket RMSE (ND) | Within-pattern weight | Global weight |")
        )
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
    md_lines.extend(_render_domain_profile(profile, algo_name=result.algorithm_name, lang=lang))

    # Declared difficulty scope: a secondary in-scope reading. It never alters
    # any score above -- it only restates what the algorithm declared it does
    # not attempt, plus how well it does inside the slice it targets.
    scope = getattr(result, "scope_report", None)
    if scope is not None and scope.declarations:
        in_acc = getattr(result, "in_scope_accuracy", None)
        total_n = len(scope.in_scope_samples) + len(scope.out_of_scope_samples)
        md_lines.append(t("#### 声明难度域内二次读数 (Declared-Scope Reading)",
                          "#### Declared-scope reading (secondary)"))
        md_lines.append(
            t(f"- **声明内容 (Declarations)**: `{scope.description}` *(经 `--declare-scope` 声明)*",
              f"- **Declarations**: `{scope.description}` *(via `--declare-scope`)*")
        )
        md_lines.append(
            t(f"- **域内样本 (In-Scope Charts)**: `{len(scope.in_scope_samples)}` / `{total_n}` 首"
              f" *(域外 {len(scope.out_of_scope_samples)} 首)*",
              f"- **In-scope charts**: `{len(scope.in_scope_samples)}` / `{total_n}` "
              f"*(out of scope: {len(scope.out_of_scope_samples)})*")
        )
        md_lines.append(
            t(f"- **让渡的真实用量占比 (Real-World Usage Given Up)**: `{scope.out_of_scope_usage * 100:.1f}%`"
              " *(据公开分析器近 90 天使用分布估算，仅供解读)*",
              f"- **Real-world usage given up**: `{scope.out_of_scope_usage * 100:.1f}%` "
              "*(estimated from the public analyser's last-90-day usage split; for interpretation only)*")
        )
        if in_acc is not None:
            md_lines.append(
                t(f"- **域内二次校准 ND-RMSE (In-Scope ND-RMSE)**: `{in_acc.global_rmse:.4f}`"
                  f" | **MAE**: `{in_acc.global_mae:.4f}`"
                  f" | **Spearman's $\\rho$**: `{in_acc.spearman_rho:.4f}`",
                  f"- **In-scope ND-RMSE**: `{in_acc.global_rmse:.4f}`"
                  f" | **MAE**: `{in_acc.global_mae:.4f}`"
                  f" | **Spearman's $\\rho$**: `{in_acc.spearman_rho:.4f}`")
            )
            md_lines.append(
                t(f"- **域内同级相对离散度 (In-Scope WTD)**: `{in_acc.mean_wtd:.4f}`\n",
                  f"- **In-scope WTD**: `{in_acc.mean_wtd:.4f}`\n")
            )
        else:
            md_lines.append("")
            md_lines.append("> [!NOTE]")
            md_lines.append(
                t("> 声明后域内样本不足两首，二次读数未计算。\n",
                  "> Fewer than two charts remain in scope, so the secondary reading is not computed.\n")
            )
        md_lines.append(
            t("> **注**: 声明不改变上方任何正式评分与六个维度，完整口径见使用指南 §7.3。\n",
              "> **Note**: a declaration never changes any score or dimension above; see usage guide section 7.3.\n")
        )

    # Macro Inversion Alarm
    if acc.calibration.macro_alerts:
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> **检测到 {len(acc.calibration.macro_alerts)} 处宏观层级倒吸违规 (Macro Inversion Alarm)**：",
              f"> **{len(acc.calibration.macro_alerts)} macro tier-inversion violation(s) detected**:")
        )
        for alert in acc.calibration.macro_alerts:
            label = t("倒吸幅度", "dip")
            md_lines.append(
                f"> - `ND {alert.lower_tier:.1f}` ({t('质心', 'centroid')} {alert.lower_anchor:.4f}) "
                f"> `ND {alert.higher_tier:.1f}` ({t('质心', 'centroid')} {alert.higher_anchor:.4f}) "
                f"| {label}: **+{alert.violation_amount:.4f}**"
            )
        md_lines.append("")

    if "calibration" in images:
        md_lines.append(f"![PAVA Calibration Curve]({images['calibration']})\n")

    # Dimension 2
    if inv is not None:
        md_lines.append(
            t(f"### 2.2 维度二：抗倒吸测试 (Anti-Rate-Inversion Monotonicity, 折扣 β {BETA_INVERSION*100:g}%)",
              f"### 2.2 Dimension 2: anti-rate-inversion monotonicity (discount β {BETA_INVERSION*100:g}%)")
        )
        md_lines.append(
            t(f"- **倒吸频率 (Inversion Frequency, IF)**: `{inv.inversion_frequency*100:.3f}%`",
              f"- **Inversion frequency (IF)**: `{inv.inversion_frequency*100:.3f}%`")
        )
        md_lines.append(
            t(f"- **倒吸曲面积分亏损 (Inversion Deficit Area, IDA)**: `{inv.auc_deficit:.6f}`",
              f"- **Inversion deficit area (IDA)**: `{inv.auc_deficit:.6f}`")
        )
        md_lines.append(
            t(f"- **最大相对倒吸跌幅 (Max Relative Drop)**: `{inv.max_relative_drop*100:.3f}%`",
              f"- **Max relative drop**: `{inv.max_relative_drop*100:.3f}%`")
        )
        md_lines.append(
            t(f"- **最大绝对难度下跌 (Max Absolute Drop)**: `{inv.max_absolute_drop:.4f}`",
              f"- **Max absolute drop**: `{inv.max_absolute_drop:.4f}`")
        )
        md_lines.append(
            t(f"- **发生倒吸的谱面数**: `{inv.inverted_maps_count}` / `{inv.total_maps_evaluated}` 首",
              f"- **Charts with an inversion**: `{inv.inverted_maps_count}` / `{inv.total_maps_evaluated}`")
        )
        md_lines.append(
            t(f"- **综合倒吸指数 (Composite Inversion Index)**: `{inv.composite_inversion_index:.4f}`",
              f"- **Composite inversion index**: `{inv.composite_inversion_index:.4f}`")
        )
        if score.s_inversion is not None:
            md_lines.append(
                t(f"- **抗倒吸得分 (AIN)**: **`{score.s_inversion:.2f}`** / 100 "
                  f"*(双曲衰减 $S = 100/(1+0.5k)$，$k = 25\\cdot IF + 120\\cdot IDA$；"
                  f"无倒吸即 100 分，衰减有长尾、不会一步到底)*",
                  f"- **Anti-inversion score (AIN)**: **`{score.s_inversion:.2f}`** / 100 "
                  f"*(hyperbolic decay $S = 100/(1+0.5k)$, $k = 25\\cdot IF + 120\\cdot IDA$; "
                  f"no inversion scores 100, and the curve has a long tail instead of a cliff)*")
            )
        md_lines.append("")
        if "inversion" in images:
            md_lines.append(f"![Rate Inversion Profiles]({images['inversion']})\n")

    # Dimension 3
    md_lines.append(
        t(f"### 2.3 维度三：抗短面测试 (Anti-Short LN / Release-Tap Artifact, 折扣 β {BETA_SHORT_LN*100:g}%)",
          f"### 2.3 Dimension 3: anti-short-LN / release-tap artifact (discount β {BETA_SHORT_LN*100:g}%)")
    )
    md_lines.append(t(f"- **短面自基准 RMSE (Short-LN RMSE)**: `{sln.short_ln_rmse:.6f}`",
                      f"- **Short-LN RMSE**: `{sln.short_ln_rmse:.6f}`"))
    md_lines.append(
        t(f"- **虚高膨胀倾向比 (Inflation Tendency Ratio, ITR)**: `{sln.inflation_tendency_ratio:+.4f}%`",
          f"- **Inflation tendency ratio (ITR)**: `{sln.inflation_tendency_ratio:+.4f}%`")
    )
    md_lines.append(t(f"- **最大难度漂移 (Max Absolute Delta)**: `{sln.max_absolute_delta:.6f}`",
                      f"- **Max absolute delta**: `{sln.max_absolute_delta:.6f}`"))
    if getattr(sln, "ho_delta_pct", None) is not None:
        md_lines.append(
            t(f"- **全长条剥离敏感度探针 (HO Probe Delta)**: `{sln.ho_delta_pct:.2f}%`",
              f"- **Full-LN hold-off probe delta (HO)**: `{sln.ho_delta_pct:.2f}%`")
        )
    md_lines.append(
        t(f"- **零响应判定 (Zero-Response Probe)**: `{'是 (输出完全未变化)' if sln.has_native_immunity else '否 (输出有变化)'}`",
          f"- **Zero-response probe**: `{'yes (output never moved)' if sln.has_native_immunity else 'no (output moved)'}`")
    )
    md_lines.append(
        t(f"- **双侧容差带与门控评级 (Band + HO Gate Grade)**: **`{sln.immunity_grade}`**",
          f"- **Two-sided band + HO gate grade**: **`{sln.immunity_grade}`**")
    )
    md_lines.append(
        t(f"- **抗短面得分 (ASLN)**: **`{sln.score:.2f}`** / 100 *(目标 ITR 7%，耦合连续 HO 长条能力门控)*\n",
          f"- **Anti-short-LN score (ASLN)**: **`{sln.score:.2f}`** / 100 *(ITR target 7%, gated by continuous HO sensitivity)*\n")
    )

    # Dimension 4
    if cont is not None:
        md_lines.append(
            t(f"### 2.4 维度四：局部连续性与断崖测试 (Local Continuity & Cliffing, 折扣 β {BETA_CONTINUITY*100:g}%)",
              f"### 2.4 Dimension 4: local continuity and cliffing (discount β {BETA_CONTINUITY*100:g}%)")
        )
        md_lines.append(
            t(f"- **断崖事件 (Cliff Events)**: `{cont.cliff_events_count}` 次 "
              f"(倍速跨度占比 `{getattr(cont, 'cliff_rate', 0.0)*100:.2f}%`)",
              f"- **Cliff events**: `{cont.cliff_events_count}` "
              f"(rate-span share `{getattr(cont, 'cliff_rate', 0.0)*100:.2f}%`)")
        )
        md_lines.append(
            t(f"- **归一化斜率粗糙度 (Normalized Slope Roughness)**: `{getattr(cont, 'mean_roughness', 0.0):.4f}`",
              f"- **Normalized slope roughness**: `{getattr(cont, 'mean_roughness', 0.0):.4f}`")
        )
        md_lines.append(
            t(f"- **最大局部利普希茨梯度 (Max Lipschitz $\\Lambda$)**: `{cont.max_lipschitz:.2f}` "
              f"(无量纲归一化 `{getattr(cont, 'max_normalized_lipschitz', 0.0):.2f}`)",
              f"- **Max local Lipschitz $\\Lambda$**: `{cont.max_lipschitz:.2f}` "
              f"(dimensionless `{getattr(cont, 'max_normalized_lipschitz', 0.0):.2f}`)")
        )
        md_lines.append(
            t(f"- **平均局部利普希茨梯度 (Mean Lipschitz)**: `{cont.mean_lipschitz:.2f}`",
              f"- **Mean local Lipschitz**: `{cont.mean_lipschitz:.2f}`")
        )
        md_lines.append(
            t(f"- **平滑度得分 (Smoothness Score)**: `{cont.smoothness_score:.2f}` / 100\n",
              f"- **Smoothness score**: `{cont.smoothness_score:.2f}` / 100\n")
        )

    # Dimension 5
    if spd is not None:
        md_lines.append(
            t(f"### 2.5 维度五：算法计算开销与计算速度 (Computation Speed, 折扣 β {BETA_SPEED*100:g}%)",
              f"### 2.5 Dimension 5: computation speed (discount β {BETA_SPEED*100:g}%)")
        )
        md_lines.append(
            t(f"- **平均计算速度 (Average Speed)**: **`{spd.average_speed_ms_per_s:.4f} ms/s`** *(每 1 秒谱面时长消耗的计算毫秒数)*",
              f"- **Average speed**: **`{spd.average_speed_ms_per_s:.4f} ms/s`** *(compute milliseconds per second of chart)*")
        )
        md_lines.append(t(f"- **中位数计算速度 (Median Speed)**: `{spd.median_speed_ms_per_s:.4f} ms/s`",
                          f"- **Median speed**: `{spd.median_speed_ms_per_s:.4f} ms/s`"))
        md_lines.append(
            t(f"- **P95 计算速度 (P95 Speed)**: `{spd.p95_speed_ms_per_s:.4f} ms/s` *(95% 的样本次于该速度；游戏场景的“最坏帧”参考)*",
              f"- **P95 speed**: `{spd.p95_speed_ms_per_s:.4f} ms/s` *(95% of samples are faster; the in-game worst frame)*")
        )
        md_lines.append(
            t(f"- **最慢样本速度 (Max Speed)**: `{spd.max_speed_ms_per_s:.4f} ms/s` *(单张谱面最慢计算速度)*",
              f"- **Slowest sample**: `{spd.max_speed_ms_per_s:.4f} ms/s` *(slowest single chart)*")
        )
        md_lines.append(
            t(f"- **实时倍率当量 (Realtime Factor)**: **`{spd.realtime_factor:.1f}x`** *(相当于实时音乐播放速率的倍数)*",
              f"- **Realtime factor**: **`{spd.realtime_factor:.1f}x`** *(how many times faster than realtime playback)*")
        )
        md_lines.append(t(f"- **计算速度得分**: **`{spd.speed_score:.1f}`** / 100",
                          f"- **Speed score**: **`{spd.speed_score:.1f}`** / 100"))
        overhead = getattr(spd, "platform_overhead_ms", 0.0) or 0.0
        if overhead > 0:
            md_lines.append(
                t(f"- **已扣除的平台兼容层开销 (Platform Overhead)**: `{overhead:.1f} ms / 次调用` "
                  f"*(wine / 进程启动等固定成本，逐谱从耗时中减去后再算速度)*",
                  f"- **Platform overhead subtracted**: `{overhead:.1f} ms per call` "
                  f"*(fixed wine / process-startup cost removed from every chart before timing)*")
            )
        md_lines.append(t(f"- **评测样本谱面数**: `{spd.evaluated_count}` 首\n",
                          f"- **Charts evaluated**: `{spd.evaluated_count}`\n"))

    # Dimension 6
    md_lines.append(
        t(f"### 2.6 维度六：抗抖动时间微扰测试 (Anti-Jitter Robustness, 折扣 β {BETA_JITTER*100:g}%)",
          f"### 2.6 Dimension 6: anti-jitter robustness (discount β {BETA_JITTER*100:g}%)")
    )
    md_lines.append(t(f"- **抖动自基准 RMSE (Jitter Self-RMSE)**: `{jit.jitter_rmse:.6f}`",
                      f"- **Jitter self-RMSE**: `{jit.jitter_rmse:.6f}`"))
    md_lines.append(t(f"- **平均绝对百分比误差 (MAPE, OD自适应微扰)**: `{jit.mape:.4f}%`",
                      f"- **MAPE (OD-adaptive jitter)**: `{jit.mape:.4f}%`"))
    md_lines.append(t(f"- **最大绝对扰动漂移 (Max Absolute Delta)**: `{jit.max_absolute_delta:.6f}`",
                      f"- **Max absolute delta**: `{jit.max_absolute_delta:.6f}`"))
    md_lines.append(t(f"- **最大相对扰动漂移 (Max Relative Delta)**: `{jit.max_relative_delta*100:.3f}%`",
                      f"- **Max relative delta**: `{jit.max_relative_delta*100:.3f}%`"))
    if getattr(jit, "arrangement_sensitivity_pct", None) is not None:
        blind = getattr(jit, "is_arrangement_blind", False)
        md_lines.append(
            t(f"- **同密度排列畸变探针响应 (Arrangement Probe Delta)**: `{jit.arrangement_sensitivity_pct:.2f}%` "
              f"*({'检测到窗口密度盲视 (<3%)' if blind else '具备正常局部排列敏感度'})*",
              f"- **Arrangement probe delta**: `{jit.arrangement_sensitivity_pct:.2f}%` "
              f"*({'density-blind window detected (<3%)' if blind else 'normal local arrangement sensitivity'})*")
        )
    md_lines.append(t(f"- **抗抖动得分 (AJ)**: **`{jit.score:.2f}`** / 100\n",
                      f"- **Anti-jitter score (AJ)**: **`{jit.score:.2f}`** / 100\n"))

    # Diagnosis
    md_lines.append(t("## 3. 算法工程诊断与优化罗盘 (Engineering Diagnosis & Recommendations)",
                      "## 3. Engineering diagnosis and recommendations"))
    md_lines.append("")
    if sln.has_native_immunity or getattr(sln, "is_brute_force_tap", False):
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> **长条盲视 / 短面零响应**: `{result.algorithm_name}` 对长条剥离（HO 探针）或 20–100ms 短面注入缺乏真实响应。"
              "说明其底层未对长音符释放做有效建模，连续 HO 能力门控已按真实长条敏感度予以扣减。",
              f"> **LN blindness / zero response**: `{result.algorithm_name}` does not really react to the full-LN "
              "hold-off probe or to 20-100 ms short-LN injection, which means it does not model LN release. The "
              "continuous HO gate has already charged that.")
        )
        md_lines.append("")

    if inv is not None and inv.inversion_frequency > 0.05:
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> **倍速单调性存在倒吸**: 当前倍速倒吸频率达 `{inv.inversion_frequency*100:.1f}%`。"
              " 建议检查物理密度积分中的非线性衰减项或分段阈值切分，防止高加速状态下的耐力/密度特征出现负梯度。",
              f"> **Rate monotonicity has inversions**: the inversion frequency is `{inv.inversion_frequency*100:.1f}%`. "
              "Check the non-linear decay term or the piecewise thresholds in the density integral; stamina/density "
              "features should not go negative under high rates.")
        )
        md_lines.append("")

    if acc.spearman_rho >= 0.85:
        md_lines.append("> [!NOTE]")
        md_lines.append(
            t(f"> **拓扑排位准确度高**: 斯皮尔曼秩相关达 `{acc.spearman_rho:.4f}`，"
              "说明算法对谱面客观难度的相对排序极其准确，具备优秀的段位排位指导价值。",
              f"> **Strong ordinal accuracy**: Spearman's rho is `{acc.spearman_rho:.4f}`, so the algorithm ranks "
              "charts by objective difficulty very accurately and is a solid guide for tier placement.")
        )
        md_lines.append("")

    failed_bm = getattr(result, "failed_beatmaps", [])
    if failed_bm:
        md_lines.append(t("## 4. 谱面异常与自动排除记录 (Excluded Failed Beatmaps)",
                          "## 4. Excluded failed beatmaps"))
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> 测试过程中检测到 **{len(failed_bm)}** 首谱面在算法计算时发生异常报错或崩溃。基准已按容错规则将其自动剔除，未计入评分：\n",
              f"> **{len(failed_bm)}** chart(s) raised or crashed during evaluation. They are excluded from scoring "
              "under the benchmark's fault-tolerance rule:\n")
        )
        md_lines.append(
            t("| 谱面文件名称 | 标定参考难度 | 键型分类 | 异常报错信息摘要 |",
              "| Chart | Reference ND | Pattern | Error summary |")
        )
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
    lang: str = "zh",
) -> Path:
    """Generate master multi-tier comparison markdown report."""
    if lang not in SUPPORTED_LANGS:
        raise ValueError(f"unsupported lang: {lang!r}")
    t = lambda zh, en: _pick(zh, en, lang)  # noqa: E731
    out_file = Path(output_path).resolve()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    images = image_paths or {}

    md_lines: list[str] = []
    md_lines.append(t(f"# CAB-VA 多档位综合评测总报告: {multi_res.algorithm_name}",
                      f"# CAB-VA multi-tier report: {multi_res.algorithm_name}"))
    md_lines.append(f"**Consistency and Accuracy Bench for VSRG Algorithm (CAB-VA) Multi-Tier Suite**\n")
    md_lines.append(t(f"> **待测算法**: `{multi_res.algorithm_name}`  ",
                      f"> **Algorithm**: `{multi_res.algorithm_name}`  "))
    md_lines.append(t(f"> **标尺类型**: `{multi_res.scale_type}`  ", f"> **Scale type**: `{multi_res.scale_type}`  "))
    md_lines.append(t(f"> **测试模式**: `{multi_res.config.mode.upper()}` (步长: $\\Delta r = {multi_res.config.rate_step}$)  ",
                      f"> **Mode**: `{multi_res.config.mode.upper()}` (step: $\\Delta r = {multi_res.config.rate_step}$)  "))
    env = collect_environment_info()
    md_lines.append(
        t(f"> **评测环境**: `{env['cpu_model']} · {env['cpu_cores']} 核 · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(速度类指标仅同环境直接可比)*  ",
          f"> **Environment**: `{env['cpu_model']} · {env['cpu_cores']} cores · {env['memory_gb']} GiB · {env['os']} · Python {env['python_version']}` *(speed metrics are only comparable within one environment)*  ")
    )
    md_lines.append(t(f"> **评测总耗时**: `{multi_res.duration_seconds:.2f}` 秒  ",
                      f"> **Total wall time**: `{multi_res.duration_seconds:.2f}` s  "))
    md_lines.append(t("> **多档位架构**: 零冗余单次执行四档切片 (Zero-Redundant 4-Tier Slicing)  ",
                      "> **Architecture**: zero-redundant 4-tier slicing in a single run  "))
    scope_report = getattr(multi_res.master_result, "scope_report", None)
    if scope_report is not None and scope_report.declarations:
        in_acc = getattr(multi_res.master_result, "in_scope_accuracy", None)
        kept = len(scope_report.in_scope_samples)
        total = kept + len(scope_report.out_of_scope_samples)
        rmse_bit = t(f"；域内二次 ND-RMSE `{in_acc.global_rmse:.4f}`" if in_acc is not None else "",
                     f"; in-scope ND-RMSE `{in_acc.global_rmse:.4f}`" if in_acc is not None else "")
        md_lines.append(
            t(f"> **声明难度域**: `{scope_report.description}`（域内 {kept}/{total} 首，让渡真实用量 {scope_report.out_of_scope_usage * 100:.1f}%{rmse_bit}）  ",
              f"> **Declared scope**: `{scope_report.description}` (in scope {kept}/{total}, usage given up "
              f"{scope_report.out_of_scope_usage * 100:.1f}%{rmse_bit})  ")
        )
    md_lines.append("")

    # Section 1: Executive 4-Tier Comparison Table
    md_lines.append(t("## 1. 四大难度档位横向对比总表 (Executive Multi-Tier Scorecard)",
                      "## 1. Executive multi-tier scorecard"))
    md_lines.append("")
    md_lines.append(
        t(f"| 评测档位 (Tier) | 难度区间 (Range) | 样本数 (Maps) | CAB-Score | 准确性 (主干) | 抗倒吸 (β {BETA_INVERSION*100:g}%) | 抗短面 (β {BETA_SHORT_LN*100:g}%) | 局部连续 (β {BETA_CONTINUITY*100:g}%) | 计算速度 (β {BETA_SPEED*100:g}%) | 抗抖动 (β {BETA_JITTER*100:g}%) | Calib RMSE |",
          f"| Tier | Range | Maps | CAB-Score | Accuracy (backbone) | Anti-Inversion (β {BETA_INVERSION*100:g}%) | Anti-Short-LN (β {BETA_SHORT_LN*100:g}%) | Continuity (β {BETA_CONTINUITY*100:g}%) | Speed (β {BETA_SPEED*100:g}%) | Anti-Jitter (β {BETA_JITTER*100:g}%) | Calib RMSE |")
    )
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
        tier_name = t(tr.tier_name, _TIER_NAME_EN.get(t_id, tr.tier_name))
        if s.is_out_of_domain:
            md_lines.append(
                f"| **{tier_name}** | {r_str} | {n_maps} | **`N/A`** | "
                f"N/A | - | N/A | - | N/A | N/A | `N/A` |"
            )
        else:
            s_inv_str = f"{s.s_inversion:.1f}" if s.s_inversion is not None else "-"
            s_cont_str = f"{s.s_continuity:.1f}" if s.s_continuity is not None else "-"
            rmse_str = f"`{tr.accuracy.global_rmse:.3f}`" if n_maps > 0 else "`N/A`"
            md_lines.append(
                f"| **{tier_name}** | {r_str} | {n_maps} | **`{s.total_score:.1f}`** / 100 | "
                f"{s.s_accuracy:.1f} | {s_inv_str} | {s.s_short_ln:.1f} | {s_cont_str} | "
                f"{s.s_speed:.1f} | {s.s_jitter:.1f} | {rmse_str} |"
            )
    md_lines.append("")
    md_lines.append(
        t("> **注**: 总分是 **ACC 主干 × 各维折扣**，不是加权和，所以维度没有「占比」可言，只有「该维归零时总分被扣掉的比例 β」。"
          "子档位仅评估静态四维 (ACC / ASLN / SPD / AJ)；动态变速指标 (AIN 与 CT) 只在全域段评定，二者的折扣直接不参与乘积。",
          "> **Note**: the total is **ACC backbone x per-dimension discounts**, not a weighted sum, so a dimension has no "
          "\"share\" - only a β, the fraction of the total lost if that dimension scored 0. Sub-tiers evaluate the four "
          "static dimensions (ACC / ASLN / SPD / AJ); the dynamic rate dimensions (AIN and CT) are only judged on the "
          "all-range tier, so their discounts drop out of the product.")
    )
    md_lines.append("")

    if "multi_tier_comparison" in images:
        md_lines.append(f"![CAB Multi-Tier Comparison]({images['multi_tier_comparison']})\n")

    # Section 2: Detailed Tier Breakdown
    md_lines.append(t("## 2. 各难度档位技术明细与表现特征 (Tier-by-Tier Technical Analysis)",
                      "## 2. Tier-by-tier technical analysis"))
    md_lines.append("")
    tier_descriptions_zh = {
        TIER_ALL: "反映算法在全域难度下的整体鲁棒性、通用性与跨阶标定能力。",
        TIER_NUMBER: "覆盖初学者到进阶段位 (Intro 1~3 至 10th Dan)，核心考察低速低密度渐进敏感度、音符密集度感知与初中级平滑度。",
        TIER_GREEK: "覆盖 Alpha 至 Epsilon ([10.5, 15.5])，属于竞技排位与段位考试核心区域。专攻高难段位的特化算法在此区间具备核心设计标定。",
        TIER_INHUMAN: "覆盖 Zeta (16.0) 至 Theta/Kappa (18+) 及以上超极限谱面，核心检验物理密度积分在超高速下的渐近发散度与稳定性。",
    }
    tier_descriptions_en = {
        TIER_ALL: "Overall robustness, generality and cross-tier calibration across the whole range.",
        TIER_NUMBER: "Intro 1-3 through 10th Dan: low-speed sensitivity, density perception and low/mid-tier smoothness.",
        TIER_GREEK: "Alpha to Epsilon ([10.5, 15.5]) - the competitive and dan-exam core.",
        TIER_INHUMAN: "Zeta (16.0) through Theta/Kappa (18+) and beyond: asymptotic behaviour of the density integral at extreme rates.",
    }

    for t_id in tier_order:
        if t_id not in multi_res.tier_results:
            continue
        tr = multi_res.tier_results[t_id]
        idx = tier_order.index(t_id) + 1
        tier_name = t(tr.tier_name, _TIER_NAME_EN.get(t_id, tr.tier_name))
        md_lines.append(f"### 2.{idx} {tier_name}")
        desc = tier_descriptions_en.get(t_id, "") if lang == "en" else tier_descriptions_zh.get(t_id, "")
        md_lines.append(t(f"> **定位说明**: {desc}\n", f"> **What it measures**: {desc}\n"))
        if tr.score.is_out_of_domain:
            md_lines.append(
                t("> [!NOTE]\n> **状态**: 该档位无可用有效谱面样本，超出算法当前指定的计算域 (`N/A`)。\n",
                  "> [!NOTE]\n> **Status**: no valid charts in this tier - outside the algorithm's declared domain (`N/A`).\n")
            )
            continue
        md_lines.append(t(f"- **综合得分**: **`{tr.score.total_score:.1f}` / 100**",
                          f"- **Total score**: **`{tr.score.total_score:.1f}` / 100**"))
        md_lines.append(
            t(f"- **校准 ND-RMSE**: `{tr.accuracy.global_rmse:.4f}` | **秩相关系数 (Spearman's $\\rho$)**: `{tr.accuracy.spearman_rho:.4f}`",
              f"- **Calibrated ND-RMSE**: `{tr.accuracy.global_rmse:.4f}` | **Spearman's $\\rho$**: `{tr.accuracy.spearman_rho:.4f}`")
        )
        wtd_str = f"{tr.accuracy.mean_wtd:.4f}"
        if tr.inversion is not None:
            inv_str = f"`{tr.inversion.inversion_frequency*100:.2f}%`"
            md_lines.append(
                t(f"- **同级相对离散度 (WTD)**: `{wtd_str}` | **倒吸频率 (IF)**: {inv_str}",
                  f"- **WTD**: `{wtd_str}` | **Inversion frequency (IF)**: {inv_str}")
            )
        else:
            md_lines.append(
                t(f"- **同级相对离散度 (WTD)**: `{wtd_str}` | **抗倒吸 (AIN)**: `-` *(仅全域段评估)*",
                  f"- **WTD**: `{wtd_str}` | **Anti-inversion (AIN)**: `-` *(all-range tier only)*")
            )

        if tr.continuity is not None:
            cont_str = t(f"`{tr.continuity.cliff_events_count}` 次", f"`{tr.continuity.cliff_events_count}`")
            md_lines.append(
                t(f"- **抗短面得分**: `{tr.score.s_short_ln:.1f}`/100 (评级: `{tr.short_ln.immunity_grade}`) | **断崖事件**: {cont_str}",
                  f"- **Anti-short-LN score**: `{tr.score.s_short_ln:.1f}`/100 (grade: `{tr.short_ln.immunity_grade}`) | **Cliff events**: {cont_str}")
            )
        else:
            md_lines.append(
                t(f"- **抗短面得分**: `{tr.score.s_short_ln:.1f}`/100 (评级: `{tr.short_ln.immunity_grade}`) | **局部连续 (CT)**: `-` *(仅全域段评估)*",
                  f"- **Anti-short-LN score**: `{tr.score.s_short_ln:.1f}`/100 (grade: `{tr.short_ln.immunity_grade}`) | **Continuity (CT)**: `-` *(all-range tier only)*")
            )

        md_lines.append(
            t(f"- **计算速度**: `{tr.speed.average_speed_ms_per_s:.4f} ms/s` ({tr.speed.realtime_factor:.1f}x 实时当量) | **抗抖动得分**: `{tr.score.s_jitter:.1f}`/100\n",
              f"- **Speed**: `{tr.speed.average_speed_ms_per_s:.4f} ms/s` ({tr.speed.realtime_factor:.1f}x realtime) | **Anti-jitter score**: `{tr.score.s_jitter:.1f}`/100\n")
        )

        # Per-tier programmatic domain profile
        tier_profile = profile_domains(
            tr.accuracy.stratified_rmse,
            pattern_counts=getattr(tr.accuracy, "stratified_counts", None),
        )
        md_lines.extend(_render_domain_profile(tier_profile, algo_name=multi_res.algorithm_name, lang=lang))

    # Section 3: Engineering Insights
    md_lines.append(t("## 3. 算法特化领域诊断与研发罗盘 (Engineering Diagnosis & Domain Fitness)",
                      "## 3. Engineering diagnosis and domain fitness"))
    md_lines.append("")
    all_tr = multi_res.tier_results.get(TIER_ALL)
    num_tr = multi_res.tier_results.get(TIER_NUMBER)
    grk_tr = multi_res.tier_results.get(TIER_GREEK)
    inh_tr = multi_res.tier_results.get(TIER_INHUMAN)

    if grk_tr and num_tr and not num_tr.score.is_out_of_domain and not grk_tr.score.is_out_of_domain:
        if grk_tr.score.total_score > num_tr.score.total_score + 8.0:
            md_lines.append("> [!TIP]")
            md_lines.append(
                t(f"> **高难段位特化算法特征**: `{multi_res.algorithm_name}` 在字母段 (Greek Dan) 得分 (`{grk_tr.score.total_score:.1f}`) "
                  f"显著高于数字段 (`{num_tr.score.total_score:.1f}`)。该算法极其适合高难竞技考试与核心段位评价，而在中低难度段有一定低估或饱和现象。",
                  f"> **High-tier specialist**: `{multi_res.algorithm_name}` scores `{grk_tr.score.total_score:.1f}` on the "
                  f"Greek tier versus `{num_tr.score.total_score:.1f}` on the number tier. It fits hard exams and core "
                  "tier placement well, while under-reading or saturating at lower difficulties.")
            )
            md_lines.append("")
        elif abs(grk_tr.score.total_score - num_tr.score.total_score) <= 5.0:
            md_lines.append("> [!NOTE]")
            md_lines.append(
                t(f"> **全域均衡型算法特征**: `{multi_res.algorithm_name}` 在数字段 (`{num_tr.score.total_score:.1f}`) 与字母段 (`{grk_tr.score.total_score:.1f}`) "
                  f"表现出高度一致的评分水准，具备出色的全谱面通用性与平滑线性映射能力。",
                  f"> **Even across the range**: `{multi_res.algorithm_name}` scores `{num_tr.score.total_score:.1f}` on the "
                  f"number tier and `{grk_tr.score.total_score:.1f}` on the Greek tier - consistent calibration and a "
                  "smooth mapping across the whole range.")
            )
            md_lines.append("")

    if inh_tr and not inh_tr.score.is_out_of_domain and inh_tr.accuracy.global_rmse > 1.8:
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> **非人类段外推发散预警**: 在非人类段 (Inhuman Dan, > 15.5)，校准误差增大至 `{inh_tr.accuracy.global_rmse:.3f}` ND。"
              " 建议优化超极限音符密度下的局部饱和函数 (如对极高 NPS 增加对数或双曲正切软上限)。",
              f"> **Extrapolation warning**: on the inhuman tier (> 15.5) the calibration error grows to "
              f"`{inh_tr.accuracy.global_rmse:.3f}` ND. Consider softening the density term at extreme NPS "
              "(a logarithmic or tanh cap).")
        )
        md_lines.append("")

    failed_bm = getattr(multi_res, "failed_beatmaps", [])
    if failed_bm:
        md_lines.append(t("## 4. 谱面异常与自动排除记录 (Excluded Failed Beatmaps)",
                          "## 4. Excluded failed beatmaps"))
        md_lines.append("> [!WARNING]")
        md_lines.append(
            t(f"> 测试过程中检测到 **{len(failed_bm)}** 首谱面在算法计算时发生异常报错或崩溃。基准已按容错规则将其自动剔除，未计入评分：\n",
              f"> **{len(failed_bm)}** chart(s) raised or crashed during evaluation; they are excluded from scoring:\n")
        )
        md_lines.append(
            t("| 谱面文件名称 | 标定参考难度 | 键型分类 | 异常报错信息摘要 |",
              "| Chart | Reference ND | Pattern | Error summary |")
        )
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


_TIER_NAME_EN = {
    TIER_ALL: "All Range",
    TIER_NUMBER: "Number Dan",
    TIER_GREEK: "Greek Dan",
    TIER_INHUMAN: "Inhuman Dan",
}
