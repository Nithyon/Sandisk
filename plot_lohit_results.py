"""
Bar-chart visualizations for Lohit's lane (LinearSVC, PCA + KNN -- Model A/B).

Reads the existing results_<model>_<stage>.json and coefficients_svm_<stage>.json
produced by train_lohit.py -- no rerun, no changes to train_lohit.py. Saves three
PNGs under plots/lohit/:

  metrics_comparison.png -- fail-class F1/Precision/Recall/PR-AUC/ROC-AUC + Accuracy
                            across all 4 experiments
  timing_comparison.png  -- preprocessing vs training time (stacked) per experiment
  svm_coefficients.png   -- top-15 |weight| LinearSVC features, stage A vs B

Usage:
    python plot_lohit_results.py
"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

OUT_DIR = os.path.join(os.path.dirname(__file__), "plots", "lohit")
os.makedirs(OUT_DIR, exist_ok=True)

EXPERIMENTS = [
    ("svm_A", "LinearSVC A", "results_svm_A.json"),
    ("svm_B", "LinearSVC B", "results_svm_B.json"),
    ("knn_A", "PCA+KNN A", "results_knn_A.json"),
    ("knn_B", "PCA+KNN B", "results_knn_B.json"),
]
COLORS = ["#4C72B0", "#55A868", "#C44E52", "#8172B2"]


def load_results():
    results = {}
    for key, label, filename in EXPERIMENTS:
        with open(filename) as f:
            results[key] = json.load(f)
    return results


def plot_metrics_comparison(results):
    labels = [label for _, label, _ in EXPERIMENTS]
    keys = [key for key, _, _ in EXPERIMENTS]

    fail_metrics = ["f1", "precision", "recall", "pr_auc", "roc_auc"]
    fail_metric_labels = ["F1", "Precision", "Recall", "PR-AUC", "ROC-AUC"]

    fig, (ax_fail, ax_acc) = plt.subplots(
        1, 2, figsize=(14, 5.5), gridspec_kw={"width_ratios": [3, 1]}
    )

    n_groups = len(fail_metrics)
    n_bars = len(keys)
    bar_width = 0.8 / n_bars
    x = range(n_groups)

    for i, key in enumerate(keys):
        values = [results[key][m] for m in fail_metrics]
        offsets = [xi + (i - (n_bars - 1) / 2) * bar_width for xi in x]
        ax_fail.bar(offsets, values, width=bar_width, label=labels[i], color=COLORS[i])

    ax_fail.set_xticks(list(x))
    ax_fail.set_xticklabels(fail_metric_labels)
    ax_fail.set_ylim(0, 1.0)
    ax_fail.set_ylabel("Score (fail class = label 1)")
    ax_fail.set_title("Fail-class metrics by experiment")
    ax_fail.legend(loc="upper right", fontsize=9)
    ax_fail.grid(axis="y", alpha=0.3)

    acc_values = [results[key]["accuracy"] for key in keys]
    ax_acc.bar(labels, acc_values, color=COLORS)
    ax_acc.set_ylim(0.95, 1.0)
    ax_acc.set_ylabel("Accuracy (overall)")
    ax_acc.set_title("Accuracy")
    ax_acc.tick_params(axis="x", rotation=30)
    ax_acc.grid(axis="y", alpha=0.3)
    for i, v in enumerate(acc_values):
        ax_acc.text(i, v + 0.001, f"{v:.3f}", ha="center", fontsize=8)

    threshold_note = " | ".join(
        f"{labels[i]}: thr={results[keys[i]]['threshold']:.3f}" for i in range(n_bars)
    )
    fig.suptitle("Lohit's lane -- Model A vs B, LinearSVC vs PCA+KNN", fontsize=13)
    fig.text(0.5, 0.005, threshold_note, ha="center", fontsize=8, color="dimgray")
    fig.tight_layout(rect=[0, 0.03, 1, 1])

    out_path = os.path.join(OUT_DIR, "metrics_comparison.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved -> {out_path}")


def plot_timing_comparison(results):
    labels = [label for _, label, _ in EXPERIMENTS]
    keys = [key for key, _, _ in EXPERIMENTS]

    preprocessing = [results[key]["preprocessing_time_min"] for key in keys]
    training = [results[key]["actual_training_time_min"] for key in keys]

    fig, ax = plt.subplots(figsize=(8, 5.5))
    ax.bar(labels, preprocessing, label="Preprocessing", color="#8C8C8C")
    ax.bar(labels, training, bottom=preprocessing, label="Training (CV + final fit)", color="#DD8452")

    for i, key in enumerate(keys):
        total = results[key]["total_time_min"]
        ax.text(i, total + 0.05, f"{total:.2f} min", ha="center", fontsize=9)

    ax.set_ylabel("Minutes")
    ax.set_title("Preprocessing vs training time by experiment")
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()

    out_path = os.path.join(OUT_DIR, "timing_comparison.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved -> {out_path}")


def plot_svm_coefficients(top_n=15):
    fig, axes = plt.subplots(1, 2, figsize=(14, 6))

    for ax, stage in zip(axes, ["A", "B"]):
        with open(f"coefficients_svm_{stage}.json") as f:
            coef_data = json.load(f)
        top = coef_data["coefficients_by_abs_weight"][:top_n]
        names = [name for name, _ in top][::-1]
        weights = [w for _, w in top][::-1]
        colors = ["#55A868" if w >= 0 else "#C44E52" for w in weights]

        ax.barh(names, weights, color=colors)
        ax.set_title(f"LinearSVC Model {stage} -- top {top_n} |weight| features")
        ax.set_xlabel("Coefficient weight")
        ax.axvline(0, color="black", linewidth=0.8)
        ax.grid(axis="x", alpha=0.3)

    fig.tight_layout()
    out_path = os.path.join(OUT_DIR, "svm_coefficients.png")
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    print(f"Saved -> {out_path}")


def main():
    results = load_results()
    plot_metrics_comparison(results)
    plot_timing_comparison(results)
    plot_svm_coefficients()


if __name__ == "__main__":
    main()
