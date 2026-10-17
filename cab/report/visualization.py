"""Visualization engine producing charts (Radar, Calibration, Rate Curves)."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Optional
import numpy as np

try:
    import matplotlib
    matplotlib.use("Agg")  # Non-interactive backend
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

from cab.runner.config import (
    DEFAULT_TIERS,
    TIER_ALL,
    TIER_DEFINITIONS,
    TIER_GREEK,
    TIER_INHUMAN,
    TIER_NUMBER,
)
from cab.runner.suite import BenchmarkRunResult, MultiTierBenchmarkResult


def generate_all_plots(result: BenchmarkRunResult | MultiTierBenchmarkResult, output_dir: str | Path) -> Dict[str, str]:
    """Generate diagnostic plots and return mapping from plot name to absolute image path."""
    if not HAS_MPL:
        return {}

    out_dir = Path(output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    paths: Dict[str, str] = {}

    # 1. Radar Chart
    try:
        radar_path = out_dir / "cab_radar_chart.png"
        plot_radar_chart(result, radar_path)
        paths["radar"] = str(radar_path)
    except Exception as e:
        print(f"Warning: Failed to generate radar chart: {e}")

    # 2. Calibration Curve
    try:
        calib_path = out_dir / "cab_calibration_curve.png"
        plot_calibration_curve(result, calib_path)
        paths["calibration"] = str(calib_path)
    except Exception as e:
        print(f"Warning: Failed to generate calibration curve: {e}")

    # 3. Inversion Profiles
    try:
        inv_path = out_dir / "cab_inversion_profiles.png"
        plot_inversion_profiles(result, inv_path)
        paths["inversion"] = str(inv_path)
    except Exception as e:
        print(f"Warning: Failed to generate inversion profiles: {e}")

    # 4. Multi-Tier Comparison (if multi-tier result)
    if isinstance(result, MultiTierBenchmarkResult):
        try:
            comp_path = out_dir / "cab_multi_tier_comparison.png"
            plot_multi_tier_comparison(result, comp_path)
            paths["multi_tier_comparison"] = str(comp_path)
        except Exception as e:
            print(f"Warning: Failed to generate multi-tier comparison chart: {e}")

    return paths


def plot_radar_chart(result: BenchmarkRunResult, output_path: Path):
    _betas = getattr(result.score, "dimension_betas", {}) or {}

    def _wpct(name: str, fallback: str) -> str:
        v = _betas.get(name)
        return f"β {round(v * 100, 2):g}%" if v is not None else fallback

    categories = [
        "Accuracy\n(backbone)",
        f"Anti-Inversion\n({_wpct('Anti-Rate-Inversion', 'β 15%')})",
        f"Anti-Short LN\n({_wpct('Anti-Short LN', 'β 15%')})",
        f"Continuity\n({_wpct('Local Continuity', 'β 10%')})",
        f"Speed\n({_wpct('Computation Speed', 'β 5%')})",
        f"Anti-Jitter\n({_wpct('Anti-Jitter', 'β 5%')})",
    ]
    raw_scores = [
        result.score.s_accuracy,
        result.score.s_inversion,
        result.score.s_short_ln,
        result.score.s_continuity,
        result.score.s_speed,
        result.score.s_jitter,
    ]
    scores = [(s if s is not None else 0.0) for s in raw_scores]

    num_vars = len(categories)
    angles = np.linspace(0, 2 * np.pi, num_vars, endpoint=False).tolist()
    scores += scores[:1]
    angles += angles[:1]

    fig, ax = plt.subplots(figsize=(6.5, 6.5), subplot_kw=dict(polar=True), dpi=150)
    ax.set_theta_offset(np.pi / 2)
    ax.set_theta_direction(-1)

    plt.xticks(angles[:-1], categories, color="#333333", size=9.5, weight="bold")
    ax.set_rlabel_position(0)
    plt.yticks([25, 50, 75, 100], ["25", "50", "75", "100"], color="#888888", size=8)
    plt.ylim(0, 100)

    # Plot data
    ax.plot(angles, scores, linewidth=2, linestyle="solid", color="#2563EB")
    ax.fill(angles, scores, color="#3B82F6", alpha=0.35)

    title = f"CAB-VA Score: {result.score.total_score:.1f}/100\nAlgorithm: {result.algorithm_name}"
    plt.title(title, size=12, color="#111827", weight="bold", y=1.1)
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close(fig)


def plot_calibration_curve(result: BenchmarkRunResult, output_path: Path):
    calib = result.accuracy.calibration
    if calib.is_nd or not calib.raw_anchors:
        return

    raw_tiers = np.array(sorted(calib.raw_anchors.keys()))
    raw_vals = np.array([calib.raw_anchors[t] for t in raw_tiers])
    pava_vals = np.array([calib.pava_anchors.get(t, calib.raw_anchors[t]) for t in raw_tiers])

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

    ax.scatter(raw_vals, raw_tiers, color="#9CA3AF", label="Raw Cohort Centroids", alpha=0.7, zorder=3)
    ax.step(pava_vals, raw_tiers, color="#EF4444", where="post", linestyle="--", label="Weighted PAVA Anchors", zorder=4)

    # Mapping curve
    if calib.conversion_formula == "daniel":
        x_dense = np.linspace(min(raw_vals), max(raw_vals), 200)
        y_ideal = x_dense - 0.5
        ax.plot(
            x_dense,
            y_ideal,
            color="#10B981",
            linewidth=2.5,
            label="Theoretical Conversion Standard: daniel - 0.5 = ND",
            zorder=5,
        )
        ax.scatter([15.5], [15.0], color="#F59E0B", s=80, zorder=6, label="Anchor Check: 15.5 <=> ND 15.0")
    elif calib.calibrator_fn:
        x_dense = np.linspace(min(pava_vals), max(pava_vals), 200)
        y_dense = calib.predict(x_dense)
        ax.plot(x_dense, y_dense, color="#2563EB", linewidth=2, label="Calibrated f_calib(y) -> ND", zorder=5)

    ax.set_xlabel(f"Algorithm Output ({result.algorithm_name})", fontsize=10, fontweight="bold")
    ax.set_ylabel("Ground Truth Difficulty (ND)", fontsize=10, fontweight="bold")
    ax.set_title("Cross-Scale Calibration: Cohort Centroids & PAVA Smoothing", fontsize=12, fontweight="bold")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(frameon=True, facecolor="white", edgecolor="#E5E7EB")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close(fig)


def plot_inversion_profiles(result: BenchmarkRunResult, output_path: Path):
    curves = result.rate_curves
    if not curves:
        return

    fig, ax = plt.subplots(figsize=(8, 5), dpi=150)

    # Pick up to 5 interesting curves
    worst_names = [m.map_name for m in result.inversion.worst_maps[:3]] if result.inversion is not None else []
    other_names = [k for k in curves if k not in worst_names][:3]
    selected_names = worst_names + other_names

    for name in selected_names[:6]:
        curve = curves[name]
        rates = [pt[0] for pt in curve]
        diffs = [pt[1] for pt in curve]
        is_worst = name in worst_names
        line_style = "--" if is_worst else "-"
        label = f"{name[:25]} (Inverted)" if is_worst else name[:25]
        ax.plot(rates, diffs, line_style, marker="o", markersize=3, label=label)

    ax.set_xlabel("Clock Rate Scaling Factor (r)", fontsize=10, fontweight="bold")
    ax.set_ylabel(f"Calculated Difficulty ({result.algorithm_name})", fontsize=10, fontweight="bold")
    ax.set_title("Clock Rate Scaling Profiles (1.01x - 2.00x)", fontsize=12, fontweight="bold")
    ax.grid(True, linestyle=":", alpha=0.6)
    ax.legend(fontsize=8, frameon=True, facecolor="white", edgecolor="#E5E7EB")
    plt.tight_layout()
    plt.savefig(output_path)
    plt.close(fig)


def plot_multi_tier_comparison(result: MultiTierBenchmarkResult, output_path: Path):
    """Generate multi-tier score breakdown grouped bar chart."""
    tier_order = [TIER_ALL, TIER_NUMBER, TIER_GREEK, TIER_INHUMAN]
    active_tiers = [t for t in tier_order if t in result.tier_results]
    if not active_tiers:
        return

    tier_labels = {
        TIER_ALL: "All Range (>= -2.5)",
        TIER_NUMBER: "Number Dan ([-2.5, 10.5))",
        TIER_GREEK: "Greek Dan ([10.5, 15.5])",
        TIER_INHUMAN: "Inhuman Dan (> 15.5)",
    }
    tier_colors = {
        TIER_ALL: "#2563EB",      # Blue
        TIER_NUMBER: "#10B981",   # Emerald
        TIER_GREEK: "#8B5CF6",    # Purple
        TIER_INHUMAN: "#EF4444",  # Red
    }

    # Compute the beta tags from the single source of truth (cab.metrics.scoring)
    # so the chart can never drift from the active scoring definition again.
    # beta = the discount applied when that dimension scores 0; ACC is the
    # backbone and carries no beta.
    from cab.metrics.scoring import (
        BETA_CONTINUITY,
        BETA_INVERSION,
        BETA_JITTER,
        BETA_SHORT_LN,
        BETA_SPEED,
    )

    def _pct(v: float) -> str:
        return f"β {round(v * 100, 2):g}%"

    metrics = [
        "CAB-Score\n(Total)",
        "Accuracy\n(backbone)",
        f"Anti-Inversion\n({_pct(BETA_INVERSION)})",
        f"Anti-Short LN\n({_pct(BETA_SHORT_LN)})",
        f"Continuity\n({_pct(BETA_CONTINUITY)})",
        f"Speed\n({_pct(BETA_SPEED)})",
        f"Anti-Jitter\n({_pct(BETA_JITTER)})",
    ]

    n_metrics = len(metrics)
    n_tiers = len(active_tiers)
    x = np.arange(n_metrics)
    width = 0.8 / n_tiers

    fig, ax = plt.subplots(figsize=(11, 6), dpi=150)

    for i, t_id in enumerate(active_tiers):
        tr = result.tier_results[t_id]
        raw_vals = [
            tr.score.total_score if not tr.score.is_out_of_domain else None,
            tr.score.s_accuracy if not tr.score.is_out_of_domain else None,
            tr.score.s_inversion,
            tr.score.s_short_ln if not tr.score.is_out_of_domain else None,
            tr.score.s_continuity,
            tr.score.s_speed if not tr.score.is_out_of_domain else None,
            tr.score.s_jitter if not tr.score.is_out_of_domain else None,
        ]
        bar_heights = [(v if v is not None else 0.0) for v in raw_vals]

        offset = (i - (n_tiers - 1) / 2) * width
        rects = ax.bar(
            x + offset,
            bar_heights,
            width,
            label=f"{tier_labels.get(t_id, t_id)}",
            color=tier_colors.get(t_id, "#6B7280"),
            alpha=0.9,
            edgecolor="white",
            linewidth=1.2,
        )
        for val, rect in zip(raw_vals, rects):
            if val is not None:
                height = rect.get_height()
                ax.annotate(
                    f"{height:.0f}",
                    xy=(rect.get_x() + rect.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7.5,
                    fontweight="bold",
                    color="#374151",
                )
            else:
                txt = "N/A" if tr.score.is_out_of_domain else "-"
                ax.annotate(
                    txt,
                    xy=(rect.get_x() + rect.get_width() / 2, 2.0),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha="center",
                    va="bottom",
                    fontsize=7.0,
                    color="#9CA3AF",
                )

    ax.set_ylabel("Standardized Score (0 - 100 pts)", fontsize=10, fontweight="bold")
    ax.set_title(
        f"CAB-VA Multi-Tier Benchmark Comparison: {result.algorithm_name} (Mode: {result.config.mode.upper()})",
        fontsize=12,
        fontweight="bold",
        pad=15,
    )
    ax.set_xticks(x)
    ax.set_xticklabels(metrics, fontsize=9.5, fontweight="bold")
    ax.set_ylim(0, 115)
    ax.grid(axis="y", linestyle=":", alpha=0.6)
    ax.legend(frameon=True, facecolor="white", edgecolor="#E5E7EB", loc="upper right")

    plt.tight_layout()
    plt.savefig(output_path)
    plt.close(fig)

