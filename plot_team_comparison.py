"""
Team-wide Model A vs Model B comparison, sourced from the shared tracker
(transcribed into team_results.csv, keyed by model_name + stage only --
no team-member names anywhere in this file, the CSV, or the generated plots).

Produces 3 PNGs under plots/team/:
  f1_by_experiment.png     -- every experiment's fail-class F1, sorted, colored by stage
  rocauc_by_experiment.png -- same, ROC-AUC
  model_a_vs_b_delta.png   -- F1(B)-F1(A) and ROC-AUC(B)-ROC-AUC(A) for each paired approach

Usage:
    python plot_team_comparison.py
"""
import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = os.path.join(os.path.dirname(__file__), "plots", "team")
os.makedirs(OUT_DIR, exist_ok=True)

# Model A -> Model B pairs to compare, as (model_a_name, model_b_name, label).
# model_name + stage is a unique key into team_results.csv (verified no collisions).
# Pairs follow the tracker's own "compare directly against" notes / matching architecture.
AB_PAIRS = [
    ("Random Forest", "Random Forest", "Random Forest"),
    ("Extra Trees", "Extra Trees", "Extra Trees"),
    ("Logistic Regression", "Logistic Regression", "Logistic Regression"),
    ("HistGradientBoosting", "HistGradientBoosting", "HistGradientBoosting"),
    ("PCA + Logistic Regression", "PCA + Logistic Regression", "PCA + LogReg"),
    ("PCA + XGBoost", "PCA + XGBoost", "PCA + XGBoost"),
    ("Linear SVM", "Linear SVM", "Linear SVM"),
    ("PCA + KNN", "PCA + KNN", "PCA + KNN"),
    ("CatBoost", "CatBoost + block statistics", "CatBoost -> +block stats"),
    ("CatBoost", "Autoencoder embedding + CatBoost", "CatBoost -> +AE"),
    ("CatBoost", "B3a + reconstruction-error feature", "CatBoost -> +AE+recon-err"),
    ("XGBoost", "XGBoost + PCA(blocks)", "XGBoost -> +PCA(blocks)"),
    ("XGBoost", "Autoencoder embedding + XGBoost", "XGBoost -> +AE"),
    ("MLP", "1D CNN block fusion", "MLP -> +CNN block fusion"),
    ("MLP", "Autoencoder embedding + MLP", "MLP -> +AE"),
]


def load_results():
    rows = []
    with open("team_results.csv", newline="") as f:
        for row in csv.DictReader(f):
            for k in ("f1", "precision", "recall", "pr_auc", "roc_auc", "accuracy", "threshold"):
                row[k] = float(row[k])
            rows.append(row)
    return rows


def label_for(row):
    return f"{row['model_name']} ({row['stage']})"


def plot_bar_by_metric(rows, metric, title, filename, xlim=None):
    MODEL_A_COLOR, MODEL_B_COLOR = "#4C72B0", "#DD8452"

    rows_sorted = sorted(rows, key=lambda r: r[metric])
    labels = [label_for(r) for r in rows_sorted]
    values = [r[metric] for r in rows_sorted]
    colors = [MODEL_A_COLOR if r["stage"] == "A" else MODEL_B_COLOR for r in rows_sorted]

    fig, ax = plt.subplots(figsize=(10, max(6, 0.32 * len(rows_sorted))))
    bars = ax.barh(labels, values, color=colors)

    for bar, v in zip(bars, values):
        ax.text(v + (xlim[1] * 0.01 if xlim else 0.005), bar.get_y() + bar.get_height() / 2,
                f"{v:.3f}", va="center", fontsize=7)

    if xlim:
        ax.set_xlim(*xlim)
    ax.set_xlabel(title)
    ax.set_title(f"{title} by experiment")

    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=MODEL_A_COLOR, label="Model A"),
        Patch(facecolor=MODEL_B_COLOR, label="Model B"),
    ]
    ax.legend(handles=legend_handles, loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(OUT_DIR, filename)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved -> {out_path}")


def plot_ab_delta(rows):
    by_key = {(r["model_name"], r["stage"]): r for r in rows}

    pairs_data = []
    for a_name, b_name, label in AB_PAIRS:
        a = by_key.get((a_name, "A"))
        b = by_key.get((b_name, "B"))
        if a is None or b is None:
            print(f"  [skip] missing data for pair: {label}")
            continue
        pairs_data.append({
            "label": label,
            "f1_delta": b["f1"] - a["f1"],
            "roc_delta": b["roc_auc"] - a["roc_auc"],
        })

    pairs_data.sort(key=lambda d: d["f1_delta"])
    labels = [d["label"] for d in pairs_data]
    f1_deltas = [d["f1_delta"] for d in pairs_data]
    roc_deltas = [d["roc_delta"] for d in pairs_data]

    fig, ax = plt.subplots(figsize=(10, max(6, 0.4 * len(pairs_data))))
    y = range(len(pairs_data))
    height = 0.38

    F1_UP, F1_DOWN = "#2ca02c", "#d62728"       # F1 delta: dark green / dark red
    ROC_UP, ROC_DOWN = "#98df8a", "#ff9896"     # ROC-AUC delta: light green / light red

    f1_colors = [F1_UP if v >= 0 else F1_DOWN for v in f1_deltas]
    roc_colors = [ROC_UP if v >= 0 else ROC_DOWN for v in roc_deltas]

    ax.barh([yi + height / 2 for yi in y], f1_deltas, height=height, color=f1_colors)
    ax.barh([yi - height / 2 for yi in y], roc_deltas, height=height, color=roc_colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels)
    ax.set_xlabel("Change from Model A to Model B (positive = improvement, negative = regression)")
    ax.set_title("Model A -> Model B: F1 and ROC-AUC delta per approach")

    # Explicit 4-entry legend -- one swatch per (metric, direction) combination actually
    # used in the chart, since a plain 2-entry legend (one per metric) can't show that
    # each metric has a separate "improved" vs "regressed" color.
    from matplotlib.patches import Patch
    legend_handles = [
        Patch(facecolor=F1_UP, label="F1 delta -- improved"),
        Patch(facecolor=F1_DOWN, label="F1 delta -- regressed"),
        Patch(facecolor=ROC_UP, label="ROC-AUC delta -- improved"),
        Patch(facecolor=ROC_DOWN, label="ROC-AUC delta -- regressed"),
    ]
    ax.legend(handles=legend_handles, loc="lower right", fontsize=8)
    ax.grid(axis="x", alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(OUT_DIR, "model_a_vs_b_delta.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved -> {out_path}")


def main():
    rows = load_results()
    plot_bar_by_metric(rows, "f1", "Fail-class F1", "f1_by_experiment.png", xlim=(0, 0.6))
    plot_bar_by_metric(rows, "roc_auc", "ROC-AUC", "rocauc_by_experiment.png", xlim=(0, 1.0))
    plot_ab_delta(rows)


if __name__ == "__main__":
    main()
