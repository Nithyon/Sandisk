"""Build the final Model A/Model B artifacts required by the project brief.

This is a reproducibility build. Hyperparameters, PCA sizes, and the decision
threshold were frozen by the earlier grouped cross-validation experiments.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import joblib
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)

from sai_nithin_features import load_split


RANDOM_STATE = 42
N_PARAM_COMPONENTS = 100
N_BLOCK_COMPONENTS = 100
THRESHOLD = 0.90
OUT = Path("deliverables")


def fit_scale_pca(train: np.ndarray, test: np.ndarray, components: int, *, wide: bool):
    mean = train.mean(axis=0, dtype=np.float32)
    std = train.std(axis=0, dtype=np.float32)
    std[std == 0] = 1.0
    train_scaled = (train - mean) / std
    test_scaled = (test - mean) / std
    pca = PCA(
        n_components=components,
        svd_solver="randomized" if wide else "auto",
        random_state=RANDOM_STATE,
    )
    train_pc = pca.fit_transform(train_scaled).astype(np.float32)
    test_pc = pca.transform(test_scaled).astype(np.float32)
    return train_pc, test_pc, {"mean": mean, "std": std, "pca": pca}


def metrics(y: np.ndarray, probability: np.ndarray) -> dict[str, float | int]:
    predicted = (probability >= THRESHOLD).astype(np.int8)
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    return {
        "fail_f1": float(f1_score(y, predicted, zero_division=0)),
        "precision": float(precision_score(y, predicted, zero_division=0)),
        "recall": float(recall_score(y, predicted, zero_division=0)),
        "pr_auc": float(average_precision_score(y, probability)),
        "roc_auc": float(roc_auc_score(y, probability)),
        "accuracy": float(accuracy_score(y, predicted)),
        "threshold": THRESHOLD,
        "tn": int(tn),
        "fp": int(fp),
        "fn": int(fn),
        "tp": int(tp),
    }


def save_probability_file(meta: pd.DataFrame, eligible: np.ndarray, y: np.ndarray,
                          probability: np.ndarray, name: str) -> None:
    frame = meta.loc[eligible, ["wafer_id", "die_row", "die_col", "old_label"]].reset_index(drop=True)
    frame["label"] = y
    frame["failure_probability"] = probability
    frame["threshold"] = THRESHOLD
    frame["predicted_label"] = (probability >= THRESHOLD).astype(np.int8)
    frame.to_csv(OUT / "predictions" / f"test_probabilities_{name}.csv", index=False)


def comparison_plot(comparison: pd.DataFrame) -> None:
    columns = ["fail_f1", "pr_auc", "roc_auc", "accuracy"]
    axis = comparison.set_index("model")[columns].T.plot(kind="bar", figsize=(10, 5), rot=0)
    axis.set_ylim(0, 1)
    axis.set_ylabel("Score")
    axis.set_title("Model A vs Model B on the shared eligible-die test set")
    axis.legend(title="")
    axis.grid(axis="y", alpha=0.25)
    axis.figure.tight_layout()
    axis.figure.savefig(OUT / "reports" / "model_A_vs_B_metrics.png", dpi=180)
    plt.close(axis.figure)


def explanation_outputs(model_a, model_b, x_a_test, x_b_test, spatial_test,
                        block_test, y_test, probability_a, probability_b, meta_test):
    report_dir = OUT / "reports"
    feature_a = [f"param_pc_{i + 1}" for i in range(N_PARAM_COMPONENTS)] + [
        "spatial_neighbor_fail_density", "spatial_radial_distance"
    ]
    feature_b = feature_a + [f"block_pc_{i + 1}" for i in range(N_BLOCK_COMPONENTS)]

    fail_indices = np.flatnonzero(y_test == 1)
    selected = int(fail_indices[np.argmax(probability_a[fail_indices])])
    contributions = x_a_test[selected] * model_a.coef_[0]
    per_die = pd.DataFrame({"feature": feature_a, "contribution": contributions})
    per_die["absolute_contribution"] = per_die["contribution"].abs()
    per_die = per_die.sort_values("absolute_contribution", ascending=False)
    per_die.to_csv(report_dir / "model_A_per_die_contributions.csv", index=False)
    top = per_die.head(20).sort_values("contribution")
    colors = np.where(top["contribution"] >= 0, "#c62828", "#2e7d32")
    fig, ax = plt.subplots(figsize=(10, 7))
    ax.barh(top["feature"], top["contribution"], color=colors)
    ax.axvline(0, color="black", linewidth=0.8)
    ax.set_title(f"Model A per-die explanation: {meta_test.iloc[selected]['wafer_id']} "
                 f"({int(meta_test.iloc[selected]['die_row'])}, {int(meta_test.iloc[selected]['die_col'])})")
    ax.set_xlabel("Log-odds contribution (red raises failure risk)")
    fig.tight_layout()
    fig.savefig(report_dir / "model_A_per_die_explanation.png", dpi=180)
    plt.close(fig)

    spatial_coef = model_a.coef_[0][N_PARAM_COMPONENTS:N_PARAM_COMPONENTS + 2]
    spatial_contribution = spatial_test @ spatial_coef
    wafer_counts = meta_test.assign(new_fail=y_test).groupby("wafer_id")["new_fail"].sum()
    wafer = wafer_counts.idxmax()
    wafer_mask = meta_test["wafer_id"].to_numpy() == wafer
    fig, ax = plt.subplots(figsize=(7, 6))
    points = ax.scatter(
        meta_test.loc[wafer_mask, "die_col"], meta_test.loc[wafer_mask, "die_row"],
        c=spatial_contribution[wafer_mask], cmap="coolwarm", marker="s", s=34,
    )
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_title(f"Model A spatial contribution by die: {wafer}")
    ax.set_xlabel("Die column")
    ax.set_ylabel("Die row")
    fig.colorbar(points, ax=ax, label="Spatial log-odds contribution")
    fig.tight_layout()
    fig.savefig(report_dir / "model_A_spatial_contribution.png", dpi=180)
    plt.close(fig)

    rng = np.random.default_rng(RANDOM_STATE)
    pass_indices = np.flatnonzero(y_test == 0)
    fail_sample = rng.choice(fail_indices, size=min(1500, len(fail_indices)), replace=False)
    pass_sample = rng.choice(pass_indices, size=min(1500, len(pass_indices)), replace=False)
    fail_mean = block_test[fail_sample].mean(axis=0)
    pass_mean = block_test[pass_sample].mean(axis=0)
    difference = fail_mean - pass_mean
    block_importance = model_b.coef_[0][-N_BLOCK_COMPONENTS:]
    fig, axes = plt.subplots(2, 1, figsize=(12, 8))
    axes[0].plot(pass_mean, label="Stayed pass", linewidth=1.2)
    axes[0].plot(fail_mean, label="New failure", linewidth=1.2)
    axes[0].set_title("Model B block-reading pattern by class")
    axes[0].set_ylabel("Mean block reading")
    axes[0].legend()
    axes[0].grid(alpha=0.2)
    axes[1].plot(difference, color="#6a1b9a", label="Failure minus pass")
    axes[1].axhline(0, color="black", linewidth=0.8)
    axes[1].set_xlabel("Block-reading position (1–2000)")
    axes[1].set_ylabel("Mean difference")
    axes[1].grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(report_dir / "model_B_block_pattern_analysis.png", dpi=180)
    plt.close(fig)
    pd.DataFrame({
        "block_component": [f"block_pc_{i + 1}" for i in range(N_BLOCK_COMPONENTS)],
        "coefficient": block_importance,
        "absolute_coefficient": np.abs(block_importance),
    }).sort_values("absolute_coefficient", ascending=False).to_csv(
        report_dir / "model_B_block_component_importance.csv", index=False
    )

    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, title, probability in zip(axes, ["Model A", "Model B"], [probability_a, probability_b]):
        ax.hist(probability[y_test == 0], bins=50, density=True, alpha=0.6, label="Stayed pass")
        ax.hist(probability[y_test == 1], bins=50, density=True, alpha=0.6, label="New failure")
        ax.axvline(THRESHOLD, color="black", linestyle="--", label="Threshold")
        ax.set_title(f"{title} probability overlap")
        ax.set_xlabel("Predicted failure probability")
        ax.set_yscale("log")
    axes[0].set_ylabel("Density (log scale)")
    axes[1].legend()
    fig.tight_layout()
    fig.savefig(report_dir / "class_imbalance_and_probability_overlap.png", dpi=180)
    plt.close(fig)


def main() -> None:
    for directory in [OUT / "models", OUT / "predictions", OUT / "reports"]:
        directory.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    train = load_split("input/train.csv", "train", True)
    test = load_split("input/test.csv", "test", True)
    train_mask = (train["meta"]["old_label"] == 0).to_numpy()
    test_mask = (test["meta"]["old_label"] == 0).to_numpy()
    train_meta = train["meta"].loc[train_mask].reset_index(drop=True)
    test_meta = test["meta"].loc[test_mask].reset_index(drop=True)
    y_train = train_meta["label"].to_numpy(dtype=np.int8)
    y_test = test_meta["label"].to_numpy(dtype=np.int8)

    param_train, param_test, param_transform = fit_scale_pca(
        train["X_param"][train_mask], test["X_param"][test_mask], N_PARAM_COMPONENTS, wide=False
    )
    spatial_train = train["X_spatial"][train_mask]
    spatial_test = test["X_spatial"][test_mask]
    x_a_train = np.hstack([param_train, spatial_train])
    x_a_test = np.hstack([param_test, spatial_test])
    model_a = LogisticRegression(class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE)
    model_a.fit(x_a_train, y_train)
    probability_a = model_a.predict_proba(x_a_test)[:, 1]

    block_train = train["X_block"][train_mask]
    block_test = test["X_block"][test_mask]
    block_train_pc, block_test_pc, block_transform = fit_scale_pca(
        block_train, block_test, N_BLOCK_COMPONENTS, wide=True
    )
    x_b_train = np.hstack([x_a_train, block_train_pc])
    x_b_test = np.hstack([x_a_test, block_test_pc])
    model_b = LogisticRegression(class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE)
    model_b.fit(x_b_train, y_train)
    probability_b = model_b.predict_proba(x_b_test)[:, 1]

    common = {
        "format_version": 1,
        "model_family": "LogisticRegression",
        "threshold": THRESHOLD,
        "eligibility_rule": "old_label == 0",
        "param_transform": param_transform,
        "spatial_feature_order": ["spatial_neighbor_fail_density", "spatial_radial_distance"],
    }
    joblib.dump({**common, "stage": "A", "model": model_a}, OUT / "models" / "model_A_logreg.joblib", compress=3)
    joblib.dump(
        {**common, "stage": "B", "block_transform": block_transform, "model": model_b},
        OUT / "models" / "model_B_logreg.joblib", compress=3,
    )

    result_a = {"model": "Model A", **metrics(y_test, probability_a)}
    result_b = {"model": "Model B", **metrics(y_test, probability_b)}
    comparison = pd.DataFrame([result_a, result_b])
    comparison.to_csv(OUT / "model_comparison.csv", index=False)
    save_probability_file(test["meta"], test_mask, y_test, probability_a, "model_A")
    save_probability_file(test["meta"], test_mask, y_test, probability_b, "model_B")
    comparison_plot(comparison)
    explanation_outputs(
        model_a, model_b, x_a_test, x_b_test, spatial_test, block_test, y_test,
        probability_a, probability_b, test_meta,
    )

    fail_count = int(y_test.sum())
    pass_count = int(len(y_test) - fail_count)
    delta_f1 = result_b["fail_f1"] - result_a["fail_f1"]
    delta_pr = result_b["pr_auc"] - result_a["pr_auc"]
    report = f"""# Final Model A / Model B analysis

## Input and output contract

- Model A uses 500 die-level measurements compressed to {N_PARAM_COMPONENTS} principal components, plus two spatial-context features.
- Model B uses the complete Model A representation plus 2,000 block readings compressed independently to {N_BLOCK_COMPONENTS} principal components.
- Each saved model returns one failure probability per eligible die (`old_label == 0`). The probability files retain wafer and die coordinates.

## Shared test-set comparison

| Model | Fail-F1 | Precision | Recall | PR-AUC | ROC-AUC | Accuracy | Threshold |
|---|---:|---:|---:|---:|---:|---:|---:|
| Model A | {result_a['fail_f1']:.6f} | {result_a['precision']:.6f} | {result_a['recall']:.6f} | {result_a['pr_auc']:.6f} | {result_a['roc_auc']:.6f} | {result_a['accuracy']:.6f} | {THRESHOLD:.2f} |
| Model B | {result_b['fail_f1']:.6f} | {result_b['precision']:.6f} | {result_b['recall']:.6f} | {result_b['pr_auc']:.6f} | {result_b['roc_auc']:.6f} | {result_b['accuracy']:.6f} | {THRESHOLD:.2f} |

Adding block readings changed Fail-F1 by {delta_f1:+.6f} and PR-AUC by {delta_pr:+.6f}. This directly measures the value of block-level information over the same die and spatial representation.

## Imbalance and overlapping distributions

The eligible test population contains {fail_count:,} new failures and {pass_count:,} passes, a failure prevalence of {fail_count / len(y_test):.2%}. Accuracy is therefore dominated by passes and cannot be used alone. Fail-F1 measures the precision/recall trade-off at the frozen threshold, while PR-AUC measures ranking quality across thresholds and is the main threshold-independent metric for the rare failure class.

The probability-overlap figure shows that many new failures receive scores in the same range as passes. This is consistent with `marginal_fail_fraction: 0.65`: most synthetic failures were deliberately generated to be close to the pass distribution. Class balancing helps the model pay attention to the rare class, but it cannot fully separate observations whose features overlap. The high precision and lower recall show the frozen threshold favors reliable failure alerts while missing a substantial fraction of subtle failures.

## Interpretation artifacts

- `reports/model_A_per_die_explanation.png` explains one high-confidence failed die through signed feature contributions.
- `reports/model_A_spatial_contribution.png` maps the spatial part of Model A across a wafer.
- `reports/model_B_block_pattern_analysis.png` compares the 2,000-reading profiles of new failures and passes.
- `reports/model_B_block_component_importance.csv` ranks the block PCA components used by Model B.

These are reproducibility results. The shared test set had already been evaluated during earlier experiments; this build packages the frozen models and regenerates their artifacts rather than claiming a new untouched test evaluation.
"""
    (OUT / "FINAL_ANALYSIS.md").write_text(report, encoding="utf-8")
    manifest = {
        "generated_seconds": time.perf_counter() - started,
        "train_eligible_rows": int(len(y_train)),
        "test_eligible_rows": int(len(y_test)),
        "model_A": result_a,
        "model_B": result_b,
        "files": sorted(str(path.relative_to(OUT)) for path in OUT.rglob("*") if path.is_file()),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
