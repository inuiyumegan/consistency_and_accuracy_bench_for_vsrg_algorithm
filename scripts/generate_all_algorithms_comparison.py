import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ALGO_METADATA = [
    ("sunny", "Star-Rating-Rebirth", "results_sunny"),
    ("mixed", "Mixed Estimator", "results_mixed"),
    ("dan-overlay", "Dan-Overlay", "results_dan_overlay"),
    ("interlude", "YAVSRG-Interlude", "results_interlude"),
    ("daniel", "Daniel", "results_daniel"),
    ("treestar", "osu-star-web", "results_treestar"),
    ("companella", "Companella", "results_companella"),
    ("minacalc", "MinaCalc-0.72.3", "results_minacalc"),
]

root = Path(__file__).resolve().parent.parent
artifact_dir = Path("/home/inui/.gemini/antigravity/brain/6d4f4d78-b257-435a-a5b5-485c393b185a")
artifact_dir.mkdir(parents=True, exist_ok=True)

data = {}
for algo_id, display_name, res_dir in ALGO_METADATA:
    json_path = root / res_dir / "cab_multi_tier_summary.json"
    if json_path.exists():
        with open(json_path, "r", encoding="utf-8") as fp:
            data[algo_id] = {
                "display_name": display_name,
                "dir": res_dir,
                "json": json.load(fp)
            }

print(f"Loaded {len(data)} algorithm results.")

# Sort algorithms by All-Range CAB-Score descending
sorted_algos = sorted(
    data.keys(),
    key=lambda a: data[a]["json"]["tiers"]["all"]["total_score"],
    reverse=True
)

# -------------------------------------------------------------
# Plot 1: Overall CAB-Score Ranking (Horizontal Bar Chart)
# -------------------------------------------------------------
plt.figure(figsize=(10, 5.5), dpi=150)
names = [data[a]["display_name"] for a in reversed(sorted_algos)]
scores = [data[a]["json"]["tiers"]["all"]["total_score"] for a in reversed(sorted_algos)]
grades = [data[a]["json"]["tiers"]["all"]["grade"] for a in reversed(sorted_algos)]

grade_colors = {
    "S+": "#F59E0B",
    "S": "#EAB308",
    "A": "#3B82F6",
    "B": "#8B5CF6",
    "C": "#10B981",
    "D": "#EF4444",
}
colors = [grade_colors.get(g, "#6B7280") for g in grades]

y_pos = np.arange(len(names))
bars = plt.barh(y_pos, scores, color=colors, alpha=0.88, edgecolor="white", height=0.65)
plt.yticks(y_pos, names, fontsize=10.5, fontweight="bold")
plt.xlabel("Standardized CAB-Score (0 - 100 points)", fontsize=10.5, fontweight="bold")
plt.title("CAB-VA Master Benchmark Ranking (8 Algorithms)", fontsize=13, fontweight="bold", pad=12)
plt.xlim(0, 105)
plt.grid(axis="x", linestyle=":", alpha=0.6)

for bar, s, g in zip(bars, scores, grades):
    w = bar.get_width()
    plt.text(w + 1.2, bar.get_y() + bar.get_height() / 2, f"{s:.1f}  [{g}]",
             va="center", ha="left", fontsize=9.5, fontweight="bold", color="#1F2937")

plt.tight_layout()
p1_path = artifact_dir / "all_algorithms_ranking_bar.png"
plt.savefig(p1_path)
plt.close()
print(f"Saved: {p1_path}")

# -------------------------------------------------------------
# Plot 2: 6-Dimension Grouped Bar Chart
# -------------------------------------------------------------
plt.figure(figsize=(14, 6.5), dpi=150)
dimensions = [
    "Ground-Truth Accuracy",
    "Anti-Rate-Inversion",
    "Anti-Short LN",
    "Local Continuity",
    "Computation Speed",
    "Anti-Jitter",
]
dim_labels = [
    "Accuracy\n(25%)",
    "Anti-Inversion\n(22%)",
    "Anti-Short LN\n(20%)",
    "Continuity\n(15%)",
    "Speed\n(10%)",
    "Anti-Jitter\n(8%)",
]

n_dims = len(dimensions)
n_algos = len(sorted_algos)
x = np.arange(n_dims)
bar_width = 0.85 / n_algos

palette = [
    "#EAB308",  # Gold (Sunny)
    "#3B82F6",  # Blue (Mixed)
    "#6366F1",  # Indigo (Dan-Overlay)
    "#8B5CF6",  # Purple (Interlude)
    "#06B6D4",  # Cyan (Daniel)
    "#10B981",  # Emerald (TreeStar)
    "#EC4899",  # Pink (Companella)
    "#EF4444",  # Red (MinaCalc)
]

for i, a_id in enumerate(sorted_algos):
    entry = data[a_id]
    scores = [
        entry["json"]["tiers"]["all"]["dimension_scores"].get(d, 0.0) or 0.0
        for d in dimensions
    ]
    offset = (i - (n_algos - 1) / 2) * bar_width
    plt.bar(
        x + offset,
        scores,
        width=bar_width,
        label=f"{entry['display_name']} ({entry['json']['tiers']['all']['total_score']:.1f})",
        color=palette[i % len(palette)],
        alpha=0.9,
        edgecolor="white",
        linewidth=0.8,
    )

plt.xticks(x, dim_labels, fontsize=10, fontweight="bold")
plt.ylabel("Standardized Score (0 - 100 points)", fontsize=10, fontweight="bold")
plt.title("Six-Dimension Performance Comparison across All 8 Algorithms (All Range)", fontsize=13, fontweight="bold", pad=12)
plt.ylim(0, 115)
plt.grid(axis="y", linestyle=":", alpha=0.6)
plt.legend(frameon=True, facecolor="white", edgecolor="#E5E7EB", loc="upper right", fontsize=8.5, ncol=2)
plt.tight_layout()
p2_path = artifact_dir / "all_algorithms_dimension_breakdown.png"
plt.savefig(p2_path)
plt.close()
print(f"Saved: {p2_path}")

# -------------------------------------------------------------
# Plot 3: 4-Tier Difficulty Fitness (All, Number, Greek, Inhuman)
# -------------------------------------------------------------
plt.figure(figsize=(13, 6), dpi=150)
tier_keys = ["all", "number", "greek", "inhuman"]
tier_names = [
    "All Range (>= -2.5)\n[6 Dimensions]",
    "Number Dan ([-2.5, 10.5))\n[4 Dimensions]",
    "Greek Dan ([10.5, 15.5])\n[4 Dimensions]",
    "Inhuman Dan (> 15.5)\n[4 Dimensions]",
]

n_tiers = len(tier_keys)
x_tier = np.arange(n_tiers)
bar_width_tier = 0.85 / n_algos

for i, a_id in enumerate(sorted_algos):
    entry = data[a_id]
    t_scores = []
    for tk in tier_keys:
        t_data = entry["json"]["tiers"].get(tk, {})
        t_scores.append(t_data.get("total_score", 0.0) if not t_data.get("is_out_of_domain", False) else 0.0)
    
    offset = (i - (n_algos - 1) / 2) * bar_width_tier
    plt.bar(
        x_tier + offset,
        t_scores,
        width=bar_width_tier,
        label=entry["display_name"],
        color=palette[i % len(palette)],
        alpha=0.9,
        edgecolor="white",
        linewidth=0.8,
    )

plt.xticks(x_tier, tier_names, fontsize=10, fontweight="bold")
plt.ylabel("Standardized Tier Score (0 - 100 points)", fontsize=10, fontweight="bold")
plt.title("Multi-Tier Difficulty Score Comparison across All 8 Algorithms", fontsize=13, fontweight="bold", pad=12)
plt.ylim(0, 115)
plt.grid(axis="y", linestyle=":", alpha=0.6)
plt.legend(frameon=True, facecolor="white", edgecolor="#E5E7EB", loc="upper right", fontsize=8.5, ncol=2)
plt.tight_layout()
p3_path = artifact_dir / "all_algorithms_tier_breakdown.png"
plt.savefig(p3_path)
plt.close()
print(f"Saved: {p3_path}")

print("All charts generated successfully in artifact directory.")
