"""
================================================================================
Model B: Logistic Regression (Die + Spatial + Block Statistics from 2,000 Readings)
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
from joblib import Parallel, delayed
from scipy.ndimage import uniform_filter
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
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


def parse_block_readings_chunk(strings):
    """
    Parses space-separated block reading strings and extracts statistical & spatial features:
    0: block_mean
    1: block_std
    2: block_w20_max (max mean of 20 spatial sub-blocks)
    3: block_w20_min (min mean of 20 spatial sub-blocks)
    4: block_w20_std (variance across sub-blocks)
    5: block_w40_max (max mean of 40 fine sub-blocks)
    6: block_peak_dev (max local deviation from global mean)
    7: block_high_anom (count of blocks > +2.5 sigma)
    8: block_low_anom (count of blocks < -2.5 sigma)
    9: block_range (max - min reading)
    """
    n = len(strings)
    feats = np.empty((n, 10), dtype=np.float32)
    for i in range(n):
        v = np.fromstring(strings[i], sep=" ", dtype=np.float32)
        m = v.mean()
        s_std = v.std()
        w20 = v.reshape(20, 100).mean(axis=1)
        w40 = v.reshape(40, 50).mean(axis=1)
        
        feats[i, 0] = m
        feats[i, 1] = s_std
        feats[i, 2] = w20.max()
        feats[i, 3] = w20.min()
        feats[i, 4] = w20.std()
        feats[i, 5] = w40.max()
        feats[i, 6] = np.max(np.abs(w40 - m))
        feats[i, 7] = np.sum(v > (m + 2.5 * s_std))
        feats[i, 8] = np.sum(v < (m - 2.5 * s_std))
        feats[i, 9] = v.max() - v.min()
    return feats


def extract_block_features_parallel(block_strings, n_jobs=8):
    """Extract block features in parallel across multiple CPU cores."""
    n = len(block_strings)
    chunk_size = int(np.ceil(n / n_jobs))
    chunks = [block_strings[i * chunk_size : (i + 1) * chunk_size] for i in range(n_jobs) if i * chunk_size < n]
    results = Parallel(n_jobs=len(chunks))(delayed(parse_block_readings_chunk)(c) for c in chunks)
    return np.vstack(results)


def main():
    start_time = time.time()
    print("=" * 80)
    print("  Model B: Logistic Regression (Die + Spatial + Block Statistics)")
    print("=" * 80)

    # 1. Load Data
    input_dir = Path("input")
    if (input_dir / "train.parquet").exists() and (input_dir / "test.parquet").exists():
        print("Loading datasets from Parquet...")
        train_df = pd.read_parquet(input_dir / "train.parquet")
        test_df = pd.read_parquet(input_dir / "test.parquet")
    else:
        print("Loading datasets from CSV...")
        train_df = pd.read_csv(input_dir / "train.csv")
        test_df = pd.read_csv(input_dir / "test.csv")

    print(f"Loaded Train: {len(train_df):,} dies | Test: {len(test_df):,} dies")

    # 2. Extract Spatial Context
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

    # 3. Filter to Eligible Dies
    train_eligible = train_df[train_df["old_label"] == 0].copy()
    test_eligible = test_df[test_df["old_label"] == 0].copy()

    # 4. Extract Block-Level Readings Features in Parallel
    print("\nExtracting sub-die block readings features (2,000 readings/die) in parallel...")
    block_col_names = [
        "block_mean", "block_std", "block_w20_max", "block_w20_min", "block_w20_std",
        "block_w40_max", "block_peak_dev", "block_high_anom", "block_low_anom", "block_range"
    ]

    t0 = time.time()
    train_block_matrix = extract_block_features_parallel(train_eligible["block_readings"].values)
    print(f"  Extracted train block features ({len(train_eligible):,} dies) in {time.time() - t0:.1f}s")

    t0 = time.time()
    test_block_matrix = extract_block_features_parallel(test_eligible["block_readings"].values)
    print(f"  Extracted test block features ({len(test_eligible):,} dies) in {time.time() - t0:.1f}s")

    for idx, col in enumerate(block_col_names):
        train_eligible[col] = train_block_matrix[:, idx]
        test_eligible[col] = test_block_matrix[:, idx]

    all_features_b = parametric_features + spatial_features + block_col_names
    print(f"\nModel B Total Feature Space: {len(parametric_features)} parametric + {len(spatial_features)} spatial + {len(block_col_names)} block = {len(all_features_b)} total features.")

    # 5. Train / Validation Split (Identical wafer split)
    X_train_raw = train_eligible[all_features_b].values
    y_train_full = train_eligible["label"].values.astype(int)

    unique_wafers = train_eligible["wafer_id"].unique()
    val_wafer_count = max(1, int(len(unique_wafers) * 0.2))
    rng = np.random.default_rng(42)
    val_wafers = rng.choice(unique_wafers, size=val_wafer_count, replace=False)
    val_mask = train_eligible["wafer_id"].isin(val_wafers).values

    X_tr_raw, y_tr = X_train_raw[~val_mask], y_train_full[~val_mask]
    X_val_raw, y_val = X_train_raw[val_mask], y_train_full[val_mask]
    X_test_raw = test_eligible[all_features_b].values
    y_test = test_eligible["label"].values.astype(int)

    # 6. Standardize 517 Features
    print("\nStandardizing 517 features with StandardScaler...")
    scaler = StandardScaler()
    X_tr = scaler.fit_transform(X_tr_raw)
    X_val = scaler.transform(X_val_raw)
    X_test = scaler.transform(X_test_raw)

    # 7. Train Balanced Logistic Regression Classifier (Model B)
    print("\nTraining Balanced Logistic Regression Classifier (Model B)...")
    logreg_b = LogisticRegression(
        C=1.0,
        class_weight="balanced",
        solver="lbfgs",
        max_iter=1000,
        random_state=42,
    )
    t0 = time.time()
    logreg_b.fit(X_tr, y_tr)
    print(f"Training completed in {time.time() - t0:.1f} seconds.")

    # 8. Threshold Optimization on Validation Fold
    print("\nOptimizing decision threshold on validation fold...")
    val_probs_b = logreg_b.predict_proba(X_val)[:, 1]
    val_pr_auc_b = average_precision_score(y_val, val_probs_b)
    print(f"Validation PR-AUC: {val_pr_auc_b:.4f}")

    precisions, recalls, thresholds = precision_recall_curve(y_val, val_probs_b)
    f1_scores = np.zeros(len(thresholds))
    for i, t in enumerate(thresholds):
        preds = (val_probs_b >= t).astype(int)
        f1_scores[i] = f1_score(y_val, preds, pos_label=1, zero_division=0)

    best_idx = np.argmax(f1_scores)
    optimal_threshold_b = thresholds[best_idx]
    best_val_f1_b = f1_scores[best_idx]
    print(f"Optimal Threshold for Model B (Logistic Regression): {optimal_threshold_b:.4f} (Validation Fail F1: {best_val_f1_b:.4f})")

    # 9. Evaluate on Test Set (Strictly Eligible Dies)
    test_probs_b = logreg_b.predict_proba(X_test)[:, 1]
    test_preds_b = (test_probs_b >= optimal_threshold_b).astype(int)

    cm_b = confusion_matrix(y_test, test_preds_b)
    actual_fail = cm_b[1, :].sum()
    actual_pass = cm_b[0, :].sum()
    pred_fail = cm_b[:, 1].sum()
    pred_pass = cm_b[:, 0].sum()

    fail_acc_b = cm_b[1, 1] / max(1, actual_fail)   # Fail Recall
    pass_acc_b = cm_b[0, 0] / max(1, actual_pass)   # Pass Recall
    fail_prec_b = cm_b[1, 1] / max(1, pred_fail)
    pass_prec_b = cm_b[0, 0] / max(1, pred_pass)
    fail_f1_b = 2 * (fail_prec_b * fail_acc_b) / max(1e-8, fail_prec_b + fail_acc_b)
    pass_f1_b = 2 * (pass_prec_b * pass_acc_b) / max(1e-8, pass_prec_b + pass_acc_b)
    overall_acc_b = (cm_b[0, 0] + cm_b[1, 1]) / max(1, len(y_test))
    test_pr_auc_b = average_precision_score(y_test, test_probs_b)
    test_roc_auc_b = roc_auc_score(y_test, test_probs_b)

    print("\n" + "=" * 80)
    print("           MODEL B (LOGISTIC REGRESSION) TEST RESULTS          ")
    print("=" * 80)
    print(f"  Total Eligible Test Dies: {len(y_test):,}")
    print(f"  Actual Fails:             {actual_fail:,} ({actual_fail / len(y_test) * 100:.2f}%)")
    print(f"  Actual Passes:            {actual_pass:,} ({actual_pass / len(y_test) * 100:.2f}%)")
    print("-" * 80)
    print(f"{'':20s} {'Pred Fail':>10s} {'Pred Pass':>10s} {'Metric':>20s} {'Value':>10s}")
    print(f"{'Actual Fail':20s} {cm_b[1, 1]:10d} {cm_b[1, 0]:10d} {'Fail Accuracy':>20s} {fail_acc_b:10.6f}")
    print(f"{'Actual Pass':20s} {cm_b[0, 1]:10d} {cm_b[0, 0]:10d} {'Pass Accuracy':>20s} {pass_acc_b:10.6f}")
    print("-" * 80)
    print(f"  Optimal Threshold:    {optimal_threshold_b:10.4f}")
    print(f"  Pass F1-Score:        {pass_f1_b:10.6f}")
    print(f"  Pass Precision:       {pass_prec_b:10.6f}")
    print(f"  Pass Accuracy (Recall){pass_acc_b:10.6f}")
    print(f"  Fail F1-Score:        {fail_f1_b:10.6f}")
    print(f"  Fail Precision:       {fail_prec_b:10.6f}")
    print(f"  Fail Accuracy (Recall){fail_acc_b:10.6f}")
    print(f"  Overall Accuracy:     {overall_acc_b:10.6f}")
    print(f"  Precision-Recall AUC: {test_pr_auc_b:10.6f}")
    print(f"  ROC-AUC:              {test_roc_auc_b:10.6f}")
    print("=" * 80)

    # 10. Model Interpretability: Top 25 Standardized Coefficients
    print("\nGenerating Model B (Logistic Regression) Interpretability Artifacts...")
    os.makedirs("reports", exist_ok=True)

    coefs_b = np.abs(logreg_b.coef_[0])
    coef_series_b = pd.Series(coefs_b, index=all_features_b)
    top25_b = coef_series_b.sort_values(ascending=False).head(25)

    plt.figure(figsize=(10, 8))
    colors_b = []
    for feat in top25_b.index[::-1]:
        if feat in block_col_names:
            colors_b.append("#5cb85c")  # Green for block
        elif feat in spatial_features:
            colors_b.append("#d9534f")  # Red for spatial
        else:
            colors_b.append("#337ab7")  # Blue for parametric

    top25_b[::-1].plot(kind="barh", color=colors_b)
    plt.title("Model B: Top 25 Absolute Coefficients (Logistic Regression)\n[Green: Block Signals | Red: Spatial Context | Blue: Parametric]", fontsize=12)
    plt.xlabel("|Standardized Logistic Coefficient|")
    plt.tight_layout()
    fi_path_b = "reports/model_b_logreg_feature_importance.png"
    plt.savefig(fi_path_b, dpi=200)
    plt.close()
    print(f"  Saved: {fi_path_b}")

    # 11. Export Model B Predictions
    print("\nExporting Model B (Logistic Regression) submission predictions...")
    all_test_block = extract_block_features_parallel(test_df["block_readings"].values)
    for idx, col in enumerate(block_col_names):
        test_df[col] = all_test_block[:, idx]

    all_test_X_raw_b = test_df[all_features_b].values
    all_test_X_scaled_b = scaler.transform(all_test_X_raw_b)
    all_test_probs_b = logreg_b.predict_proba(all_test_X_scaled_b)[:, 1]
    final_preds_b = np.where(test_df["old_label"].values == 1, 1, (all_test_probs_b >= optimal_threshold_b).astype(int))

    sub_df_b = pd.DataFrame({
        "wafer_id": test_df["wafer_id"],
        "die_row": test_df["die_row"],
        "die_col": test_df["die_col"],
        "predicted_label": final_preds_b,
    })
    sub_path_b = "predictions_model_b_logreg.csv"
    sub_df_b.to_csv(sub_path_b, index=False)
    print(f"  Saved: {sub_path_b} ({len(sub_df_b):,} predictions)")

    print(f"\nAll operations completed successfully in {time.time() - start_time:.1f}s.")


if __name__ == "__main__":
    main()
