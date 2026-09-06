"""
================================================================================
Model A: HistGradientBoosting (500 Parametric Features + Spatial Context)
Multi-Resolution Die Yield Prediction Hackathon
================================================================================
"""

import os
import sys
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
)


def compute_spatial_features(df):
    """Computes wafer spatial context features."""
    dfs = []
    for wafer_id, w_df in df.groupby("wafer_id", sort=False):
        w = w_df.copy()
        r = w["die_row"].values
        c = w["die_col"].values

        r_min, r_max = r.min(), r.max()
        c_min, c_max = c.min(), c.max()
        r_mid = (r_min + r_max) / 2.0
        c_mid = (c_min + c_max) / 2.0

        dr = r - r_mid
        dc = c - c_mid
        radius = np.sqrt(dr**2 + dc**2)
        max_rad = radius.max() if radius.max() > 0 else 1.0
        w["radial_dist"] = radius / max_rad
        w["angle_rad"] = np.arctan2(dr, dc)

        w["norm_row"] = (r - r_min) / max(1, r_max - r_min)
        w["norm_col"] = (c - c_min) / max(1, c_max - c_min)

        grid_rows, grid_cols = r_max + 1, c_max + 1
        fail_grid = np.zeros((grid_rows, grid_cols), dtype=float)
        valid_grid = np.zeros((grid_rows, grid_cols), dtype=float)
        fail_grid[r, c] = w["old_label"].values
        valid_grid[r, c] = 1.0

        for sz in [3, 5]:
            fail_sum = uniform_filter(fail_grid, size=sz, mode="constant", cval=0.0)
            valid_sum = uniform_filter(valid_grid, size=sz, mode="constant", cval=0.0)
            with np.errstate(divide="ignore", invalid="ignore"):
                density = np.where(valid_sum > 0, fail_sum / valid_sum, 0.0)
            w[f"neigh_fail_density_{sz}"] = density[r, c]

        w["wafer_pretest_fail_rate"] = w["old_label"].mean()
        dfs.append(w)

    return pd.concat(dfs, ignore_index=True)


def load_dataset():
    """Load train and test data."""
    input_dir = Path("input")
    if (input_dir / "train.parquet").exists() and (input_dir / "test.parquet").exists():
        print("Loading train and test datasets from Parquet...")
        train_df = pd.read_parquet(input_dir / "train.parquet")
        test_df = pd.read_parquet(input_dir / "test.parquet")
    else:
        print("Loading train and test datasets from CSV...")
        train_df = pd.read_csv(input_dir / "train.csv")
        test_df = pd.read_csv(input_dir / "test.csv")

    drop_cols = [c for c in ["block_readings"] if c in train_df.columns]
    if drop_cols:
        train_df.drop(columns=drop_cols, inplace=True)
        test_df.drop(columns=drop_cols, inplace=True)

    return train_df, test_df


def main():
    start_time = time.time()
    print("=" * 80)
    print("  Model A: HistGradientBoosting (500 Parametric Features + Spatial Context)")
    print("=" * 80)

    # 1. Load Data
    train_df, test_df = load_dataset()
    print(f"Loaded Train: {len(train_df):,} dies | Test: {len(test_df):,} dies")

    # 2. Extract Spatial Features
    print("\nComputing spatial context features...")
    train_df = compute_spatial_features(train_df)
    test_df = compute_spatial_features(test_df)

    spatial_features = [
        "radial_dist",
        "angle_rad",
        "norm_row",
        "norm_col",
        "neigh_fail_density_3",
        "neigh_fail_density_5",
        "wafer_pretest_fail_rate",
    ]
    parametric_features = [c for c in train_df.columns if c.startswith("feature_")]
    all_features = parametric_features + spatial_features
    print(f"Feature space: {len(parametric_features)} parametric + {len(spatial_features)} spatial = {len(all_features)} total features.")

    # 3. Filter to Eligible Dies (old_label == 0)
    train_eligible = train_df[train_df["old_label"] == 0].copy()
    test_eligible = test_df[test_df["old_label"] == 0].copy()

    X_train_full = train_eligible[all_features].values
    y_train_full = train_eligible["label"].values.astype(int)

    # Stratified Wafer Validation Split
    unique_wafers = train_eligible["wafer_id"].unique()
    val_wafer_count = max(1, int(len(unique_wafers) * 0.2))
    rng = np.random.default_rng(42)
    val_wafers = rng.choice(unique_wafers, size=val_wafer_count, replace=False)
    val_mask = train_eligible["wafer_id"].isin(val_wafers).values

    X_tr, y_tr = X_train_full[~val_mask], y_train_full[~val_mask]
    X_val, y_val = X_train_full[val_mask], y_train_full[val_mask]
    X_test = test_eligible[all_features].values
    y_test = test_eligible["label"].values.astype(int)

    # 4. Train Balanced HistGradientBoosting Classifier
    print("\nTraining Balanced HistGradientBoosting Classifier...")
    hgb = HistGradientBoostingClassifier(
        max_iter=150,
        max_depth=10,
        min_samples_leaf=20,
        learning_rate=0.08,
        l2_regularization=1.0,
        class_weight="balanced",
        random_state=42,
    )
    t0 = time.time()
    hgb.fit(X_tr, y_tr)
    print(f"Training completed in {time.time() - t0:.1f} seconds.")

    # 5. Threshold Optimization on Validation Fold
    print("\nOptimizing decision threshold on validation fold...")
    val_probs = hgb.predict_proba(X_val)[:, 1]
    val_pr_auc = average_precision_score(y_val, val_probs)
    print(f"Validation PR-AUC: {val_pr_auc:.4f}")

    precisions, recalls, thresholds = precision_recall_curve(y_val, val_probs)
    f1_scores = np.zeros(len(thresholds))
    for i, t in enumerate(thresholds):
        preds = (val_probs >= t).astype(int)
        f1_scores[i] = f1_score(y_val, preds, pos_label=1, zero_division=0)

    best_idx = np.argmax(f1_scores)
    optimal_threshold = thresholds[best_idx]
    best_val_f1 = f1_scores[best_idx]
    print(f"Optimal Threshold for HistGradientBoosting: {optimal_threshold:.4f} (Validation Fail F1: {best_val_f1:.4f})")

    # 6. Evaluate on Test Set (Strictly Eligible Dies)
    test_probs = hgb.predict_proba(X_test)[:, 1]
    test_preds = (test_probs >= optimal_threshold).astype(int)

    cm = confusion_matrix(y_test, test_preds)
    actual_fail = cm[1, :].sum()
    actual_pass = cm[0, :].sum()
    pred_fail = cm[:, 1].sum()
    pred_pass = cm[:, 0].sum()

    fail_acc = cm[1, 1] / max(1, actual_fail)   # Fail Recall
    pass_acc = cm[0, 0] / max(1, actual_pass)   # Pass Recall
    fail_prec = cm[1, 1] / max(1, pred_fail)
    pass_prec = cm[0, 0] / max(1, pred_pass)
    fail_f1 = 2 * (fail_prec * fail_acc) / max(1e-8, fail_prec + fail_acc)
    pass_f1 = 2 * (pass_prec * pass_acc) / max(1e-8, pass_prec + pass_acc)
    overall_acc = (cm[0, 0] + cm[1, 1]) / max(1, len(y_test))
    test_pr_auc = average_precision_score(y_test, test_probs)
    test_roc_auc = roc_auc_score(y_test, test_probs)

    print("\n" + "=" * 80)
    print("          MODEL A (HIST GRADIENT BOOSTING) TEST RESULTS         ")
    print("=" * 80)
    print(f"  Total Eligible Test Dies: {len(y_test):,}")
    print(f"  Actual Fails:             {actual_fail:,} ({actual_fail / len(y_test) * 100:.2f}%)")
    print(f"  Actual Passes:            {actual_pass:,} ({actual_pass / len(y_test) * 100:.2f}%)")
    print("-" * 80)
    print(f"{'':20s} {'Pred Fail':>10s} {'Pred Pass':>10s} {'Metric':>20s} {'Value':>10s}")
    print(f"{'Actual Fail':20s} {cm[1, 1]:10d} {cm[1, 0]:10d} {'Fail Accuracy':>20s} {fail_acc:10.6f}")
    print(f"{'Actual Pass':20s} {cm[0, 1]:10d} {cm[0, 0]:10d} {'Pass Accuracy':>20s} {pass_acc:10.6f}")
    print("-" * 80)
    print(f"  Optimal Threshold:    {optimal_threshold:10.4f}")
    print(f"  Pass F1-Score:        {pass_f1:10.6f}")
    print(f"  Pass Precision:       {pass_prec:10.6f}")
    print(f"  Pass Accuracy (Recall){pass_acc:10.6f}")
    print(f"  Fail F1-Score:        {fail_f1:10.6f}")
    print(f"  Fail Precision:       {fail_prec:10.6f}")
    print(f"  Fail Accuracy (Recall){fail_acc:10.6f}")
    print(f"  Overall Accuracy:     {overall_acc:10.6f}")
    print(f"  Precision-Recall AUC: {test_pr_auc:10.6f}")
    print(f"  ROC-AUC:              {test_roc_auc:10.6f}")
    print("=" * 80)

    # 7. Permutation Feature Importance on Subsample
    print("\nComputing Feature Importance on validation set...")
    os.makedirs("reports", exist_ok=True)
    val_sample_idx = rng.choice(len(X_val), size=min(3000, len(X_val)), replace=False)
    perm_res = permutation_importance(hgb, X_val[val_sample_idx], y_val[val_sample_idx], n_repeats=3, random_state=42, n_jobs=-1)
    
    feat_series = pd.Series(perm_res.importances_mean, index=all_features)
    top25 = feat_series.sort_values(ascending=False).head(25)

    plt.figure(figsize=(10, 8))
    colors = ["#d9534f" if feat in spatial_features else "#337ab7" for feat in top25.index[::-1]]
    top25[::-1].plot(kind="barh", color=colors)
    plt.title("Model A: Top 25 Permutation Feature Importances (HistGradientBoosting)\n[Red: Spatial Context | Blue: Parametric]", fontsize=12)
    plt.xlabel("Mean Accuracy Decrease on Permutation")
    plt.tight_layout()
    fi_path = "reports/model_a_hgb_feature_importance.png"
    plt.savefig(fi_path, dpi=200)
    plt.close()
    print(f"  Saved: {fi_path}")

    # 8. Export Submission Predictions
    print("\nExporting Model A (HistGradientBoosting) submission predictions...")
    all_test_X = test_df[all_features].values
    all_test_probs = hgb.predict_proba(all_test_X)[:, 1]
    final_preds = np.where(test_df["old_label"].values == 1, 1, (all_test_probs >= optimal_threshold).astype(int))

    submission_df = pd.DataFrame({
        "wafer_id": test_df["wafer_id"],
        "die_row": test_df["die_row"],
        "die_col": test_df["die_col"],
        "predicted_label": final_preds,
    })
    sub_path = "predictions_model_a_hgb.csv"
    submission_df.to_csv(sub_path, index=False)
    print(f"  Saved: {sub_path} ({len(submission_df):,} predictions)")

    print(f"\nAll operations completed successfully in {time.time() - start_time:.1f}s.")


if __name__ == "__main__":
    main()
