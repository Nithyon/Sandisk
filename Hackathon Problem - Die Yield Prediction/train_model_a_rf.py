"""
================================================================================
Model A: Random Forest with 500 Parametric Features + Spatial Context
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
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    average_precision_score,
    confusion_matrix,
    f1_score,
    precision_recall_curve,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold, train_test_split


def compute_spatial_features(df):
    """
    Computes spatial context features for each die on each wafer:
    1. radial_dist: Normalized distance from wafer center
    2. angle_rad: Polar angle (-pi to pi) around wafer center
    3. norm_row, norm_col: Min-max normalized coordinates
    4. neigh_fail_density_3x3: Local fail density in 3x3 window around die
    5. neigh_fail_density_5x5: Local fail density in 5x5 window around die
    6. wafer_pretest_fail_rate: Pre-test failure proportion of the parent wafer
    """
    dfs = []
    for wafer_id, w_df in df.groupby("wafer_id", sort=False):
        w = w_df.copy()
        r = w["die_row"].values
        c = w["die_col"].values

        r_min, r_max = r.min(), r.max()
        c_min, c_max = c.min(), c.max()
        r_mid = (r_min + r_max) / 2.0
        c_mid = (c_min + c_max) / 2.0

        # Radial distance and angle
        dr = r - r_mid
        dc = c - c_mid
        radius = np.sqrt(dr**2 + dc**2)
        max_rad = radius.max() if radius.max() > 0 else 1.0
        w["radial_dist"] = radius / max_rad
        w["angle_rad"] = np.arctan2(dr, dc)

        # Normalized coordinates
        w["norm_row"] = (r - r_min) / max(1, r_max - r_min)
        w["norm_col"] = (c - c_min) / max(1, c_max - c_min)

        # 2D Grid for Neighborhood Defect Density
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
    """Load train and test data, preferentially using fast Parquet if available."""
    input_dir = Path("input")
    
    # Check parquet first
    if (input_dir / "train.parquet").exists() and (input_dir / "test.parquet").exists():
        print("Loading train and test datasets from Parquet...")
        train_df = pd.read_parquet(input_dir / "train.parquet")
        test_df = pd.read_parquet(input_dir / "test.parquet")
    elif (input_dir / "train.csv").exists() and (input_dir / "test.csv").exists():
        print("Loading train and test datasets from CSV...")
        train_df = pd.read_csv(input_dir / "train.csv")
        test_df = pd.read_csv(input_dir / "test.csv")
    else:
        print("ERROR: train and test files not found in 'input/' directory!")
        sys.exit(1)

    # Exclude raw block readings column for Model A to conserve memory
    drop_cols = [c for c in ["block_readings"] if c in train_df.columns]
    if drop_cols:
        train_df.drop(columns=drop_cols, inplace=True)
        test_df.drop(columns=drop_cols, inplace=True)

    return train_df, test_df


def main():
    start_time = time.time()
    print("=" * 70)
    print("  Model A: Random Forest (500 Parametric Features + Spatial Context)")
    print("=" * 70)

    # 1. Load Data
    train_df, test_df = load_dataset()
    print(f"Loaded Train: {len(train_df):,} dies across {train_df['wafer_id'].nunique()} wafers")
    print(f"Loaded Test:  {len(test_df):,} dies across {test_df['wafer_id'].nunique()} wafers")

    # 2. Extract Spatial Context Features
    print("\nComputing spatial context features (radial distance, neighborhood density, coordinates)...")
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

    # 3. Filter to Eligible Dies (old_label == 0) for training and evaluation
    train_eligible = train_df[train_df["old_label"] == 0].copy()
    test_eligible = test_df[test_df["old_label"] == 0].copy()

    n_train_eligible = len(train_eligible)
    n_train_fail = train_eligible["label"].sum()
    print(f"\nTrain Eligible Dies: {n_train_eligible:,} | New Fails: {n_train_fail:,} ({n_train_fail / n_train_eligible * 100:.2f}%)")

    n_test_eligible = len(test_eligible)
    n_test_fail = test_eligible["label"].sum()
    print(f"Test Eligible Dies:  {n_test_eligible:,} | New Fails: {n_test_fail:,} ({n_test_fail / n_test_eligible * 100:.2f}%)")

    X_train_full = train_eligible[all_features].values
    y_train_full = train_eligible["label"].values.astype(int)

    # Train / Validation Split (Stratified by wafer to prevent leakage across dies of same wafer)
    unique_wafers = train_eligible["wafer_id"].unique()
    val_wafer_count = max(1, int(len(unique_wafers) * 0.2))
    rng = np.random.default_rng(42)
    val_wafers = rng.choice(unique_wafers, size=val_wafer_count, replace=False)
    val_mask = train_eligible["wafer_id"].isin(val_wafers).values

    X_tr, y_tr = X_train_full[~val_mask], y_train_full[~val_mask]
    X_val, y_val = X_train_full[val_mask], y_train_full[val_mask]
    print(f"Training split: {len(X_tr):,} dies | Validation split: {len(X_val):,} dies ({val_wafer_count} wafers)")

    # 4. Train Random Forest Model
    print("\nTraining Balanced Random Forest Classifier...")
    rf = RandomForestClassifier(
        n_estimators=120,
        max_depth=18,
        min_samples_leaf=4,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )
    t0 = time.time()
    rf.fit(X_tr, y_tr)
    print(f"Training completed in {time.time() - t0:.1f} seconds.")

    # 5. Threshold Optimization on Validation Set
    print("\nOptimizing decision threshold on validation fold...")
    val_probs = rf.predict_proba(X_val)[:, 1]
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
    print(f"Optimal Threshold: {optimal_threshold:.4f} (Validation Fail F1: {best_val_f1:.4f})")

    # 6. Evaluate on Test Set (Strictly on Eligible Dies)
    X_test = test_eligible[all_features].values
    y_test = test_eligible["label"].values.astype(int)

    test_probs = rf.predict_proba(X_test)[:, 1]
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

    print("\n" + "=" * 70)
    print("                MODEL A (RANDOM FOREST) TEST RESULTS                ")
    print("=" * 70)
    print(f"  Total Eligible Test Dies: {len(y_test):,}")
    print(f"  Actual Fails:             {actual_fail:,} ({actual_fail / len(y_test) * 100:.2f}%)")
    print(f"  Actual Passes:            {actual_pass:,} ({actual_pass / len(y_test) * 100:.2f}%)")
    print("-" * 70)
    print(f"{'':20s} {'Pred Fail':>10s} {'Pred Pass':>10s} {'Metric':>20s} {'Value':>10s}")
    print(f"{'Actual Fail':20s} {cm[1, 1]:10d} {cm[1, 0]:10d} {'Fail Accuracy':>20s} {fail_acc:10.6f}")
    print(f"{'Actual Pass':20s} {cm[0, 1]:10d} {cm[0, 0]:10d} {'Pass Accuracy':>20s} {pass_acc:10.6f}")
    print("-" * 70)
    print(f"  Fail Precision:      {fail_prec:10.6f}")
    print(f"  Pass Precision:      {pass_prec:10.6f}")
    print(f"  Fail F1-Score:       {fail_f1:10.6f}")
    print(f"  Pass F1-Score:       {pass_f1:10.6f}")
    print(f"  Overall Accuracy:    {overall_acc:10.6f}")
    print(f"  Precision-Recall AUC:{test_pr_auc:10.6f}")
    print(f"  ROC-AUC:             {test_roc_auc:10.6f}")
    print("=" * 70)

    # 7. Model Interpretability: Feature Importance Ranking
    print("\nGenerating Model Interpretability Artifacts...")
    os.makedirs("reports", exist_ok=True)

    importances = rf.feature_importances_
    feat_series = pd.Series(importances, index=all_features)
    top25 = feat_series.sort_values(ascending=False).head(25)

    plt.figure(figsize=(10, 8))
    colors = ["#d9534f" if feat in spatial_features else "#337ab7" for feat in top25.index[::-1]]
    top25[::-1].plot(kind="barh", color=colors)
    plt.title("Model A: Top 25 Feature Importances (Random Forest)\n[Red: Spatial Context | Blue: Parametric]", fontsize=12)
    plt.xlabel("Gini Feature Importance")
    plt.tight_layout()
    fi_path = "reports/model_a_feature_importance.png"
    plt.savefig(fi_path, dpi=200)
    plt.close()
    print(f"  Saved: {fi_path}")

    # Spatial Probability Heatmap for Sample Wafer
    sample_wafer_id = test_df["wafer_id"].iloc[0]
    sample_w = test_df[test_df["wafer_id"] == sample_wafer_id].copy()
    sample_X = sample_w[all_features].values
    sample_probs = rf.predict_proba(sample_X)[:, 1]

    # For old_label == 1, probability is 1.0
    final_probs = np.where(sample_w["old_label"].values == 1, 1.0, sample_probs)

    r_max = sample_w["die_row"].max() + 1
    c_max = sample_w["die_col"].max() + 1
    prob_map = np.full((r_max, c_max), np.nan)
    actual_map = np.full((r_max, c_max), np.nan)

    r_idx = sample_w["die_row"].values
    c_idx = sample_w["die_col"].values
    prob_map[r_idx, c_idx] = final_probs
    actual_map[r_idx, c_idx] = sample_w["label"].values

    fig, axes = plt.subplots(1, 2, figsize=(12, 5))
    im0 = axes[0].imshow(actual_map, cmap="coolwarm", vmin=0, vmax=1)
    axes[0].set_title(f"Actual Die State ({sample_wafer_id})\n0=Pass, 1=Fail")
    fig.colorbar(im0, ax=axes[0], fraction=0.046, pad=0.04)

    im1 = axes[1].imshow(prob_map, cmap="viridis", vmin=0, vmax=1)
    axes[1].set_title(f"Model A Predicted Failure Probability\n({sample_wafer_id})")
    fig.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)
    plt.tight_layout()

    hm_path = "reports/model_a_spatial_heatmap.png"
    plt.savefig(hm_path, dpi=200)
    plt.close()
    print(f"  Saved: {hm_path}")

    # 8. Export Submission Predictions
    print("\nExporting Model A submission predictions...")
    all_test_X = test_df[all_features].values
    all_test_probs = rf.predict_proba(all_test_X)[:, 1]
    
    # Pre-test fails trivially remain 1; eligible dies use thresholded prediction
    final_preds = np.where(test_df["old_label"].values == 1, 1, (all_test_probs >= optimal_threshold).astype(int))
    
    submission_df = pd.DataFrame({
        "wafer_id": test_df["wafer_id"],
        "die_row": test_df["die_row"],
        "die_col": test_df["die_col"],
        "predicted_label": final_preds,
    })
    
    sub_path = "predictions_model_a.csv"
    submission_df.to_csv(sub_path, index=False)
    print(f"  Saved: {sub_path} ({len(submission_df):,} predictions)")

    print(f"\nAll operations completed successfully in {time.time() - start_time:.1f}s.")


if __name__ == "__main__":
    main()
