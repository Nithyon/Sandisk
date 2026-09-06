"""
Lohit's experiment lane: LinearSVC, PCA + KNN -- Model A (die-level + spatial)
and Model B (+ PCA(block readings)).

Reuses the team's shared preprocessing (sai_nithin_features.load_split):
spatial-feature construction and block_readings parsing are NOT
reimplemented here, so results stay comparable across lanes.

Follows the tracker's shared rules and the settled team convention
(COORDINATION_LOG.md, "PCA DEFINITION DECISION" / "SAI NITHIN PIPELINE
IMPLEMENTATION" checkpoints):
  - Evaluate only dies with old_label == 0 (eligible dies).
  - Spatial features built on the FULL wafer, filtered to old_label == 0 after.
  - Spatial features are NEVER put through PCA.
  - GroupKFold(5) on wafer_id, random_state=42.
  - Threshold chosen from out-of-fold validation predictions, not blind 0.5.
  - Score test.csv ONCE with the model frozen.
  - Report Precision/Recall/F1/PR-AUC/ROC-AUC on the FAIL class, Accuracy overall.

Model definitions (Lohit's lane):
  - LinearSVC Model A = scaled 500 parametric features + raw spatial features
    (no PCA -- LinearSVC is linear and doesn't need dimensionality reduction).
  - LinearSVC Model B = exact Model A representation + a separately fitted
    PCA on the 2000 block readings.
  - PCA+KNN Model A  = PCA(~50) on the scaled 500 parametric features + raw
    spatial features (KNN needs the dimensionality cut; LinearSVC doesn't).
  - PCA+KNN Model B  = exact Model A representation + a separately fitted
    PCA on the 2000 block readings.

Starting hyperparameters (per the team plan):
  - LinearSVC: C=0.1, class_weight="balanced".
  - KNN: n_neighbors=25, weights="distance"; PCA to ~50 dims before KNN.
  - Block-branch PCA (Model B only, both models): 100 components, matching
    the team's existing block-PCA cap.

Usage:
    venv\\Scripts\\python.exe train_lohit.py --model svm --stage A
    venv\\Scripts\\python.exe train_lohit.py --model knn --stage B
    venv\\Scripts\\python.exe train_lohit.py --model svm --stage A --smoke 5000
"""
import argparse
import gc
import json
import time

import numpy as np
from sklearn.decomposition import PCA
from sklearn.model_selection import GroupKFold
from sklearn.svm import LinearSVC
from sklearn.neighbors import KNeighborsClassifier
from sklearn.metrics import (
    f1_score, precision_score, recall_score,
    average_precision_score, roc_auc_score, accuracy_score,
)

from sai_nithin_features import load_split, FEATURE_COLS

N_SPLITS = 5
RANDOM_STATE = 42
PARAM_PCA_COMPONENTS = 50   # KNN only -- "PCA to ~50 dims" starting point
BLOCK_PCA_COMPONENTS = 100  # Model B only (both models) -- matches team's block-PCA cap


def _standardize_f32(X_train, X_eval):
    """Fit mean/std on train only, apply to both. Kept local (not imported from
    train_sai_nithin.py) so this lane stays independently runnable of teammates'
    training scripts -- only the expensive shared parsing/spatial code in
    sai_nithin_features.py is reused, per the coordination log's ownership split."""
    mean = X_train.mean(axis=0, dtype=np.float32)
    std = X_train.std(axis=0, dtype=np.float32)
    std[std == 0] = 1.0
    return (X_train - mean) / std, (X_eval - mean) / std


def _pca_transform(X_train, X_eval, n_components, wide):
    """Fit PCA on train only, transform both. `wide=True` (the 2000-column
    block branch) uses randomized SVD for memory/time efficiency."""
    pca = PCA(
        n_components=n_components,
        svd_solver="randomized" if wide else "auto",
        random_state=RANDOM_STATE,
    )
    train_t = pca.fit_transform(X_train).astype(np.float32)
    eval_t = pca.transform(X_eval).astype(np.float32)
    return train_t, eval_t


def build_representation(model_name, X_param_train, X_spatial_train, X_block_train,
                          X_param_eval, X_spatial_eval, X_block_eval, stage):
    """
    Fit scaler/PCA on the TRAIN portion only, transform both train and eval.

    svm Model A: scaled param + spatial (no PCA)
    svm Model B: svm Model A representation + PCA(block)
    knn Model A: PCA(param, ~50) + spatial
    knn Model B: knn Model A representation + PCA(block)
    """
    param_train_s, param_eval_s = _standardize_f32(X_param_train, X_param_eval)

    if model_name == "svm":
        param_train_rep, param_eval_rep = param_train_s, param_eval_s
    else:  # knn
        param_train_rep, param_eval_rep = _pca_transform(
            param_train_s, param_eval_s, PARAM_PCA_COMPONENTS, wide=False
        )
    del param_train_s, param_eval_s

    train_parts = [param_train_rep, X_spatial_train]
    eval_parts = [param_eval_rep, X_spatial_eval]

    if stage == "B":
        block_train_s, block_eval_s = _standardize_f32(X_block_train, X_block_eval)
        block_train_pca, block_eval_pca = _pca_transform(
            block_train_s, block_eval_s, BLOCK_PCA_COMPONENTS, wide=True
        )
        del block_train_s, block_eval_s
        train_parts.append(block_train_pca)
        eval_parts.append(block_eval_pca)
        gc.collect()

    return np.hstack(train_parts), np.hstack(eval_parts)


def make_model(model_name):
    if model_name == "svm":
        return LinearSVC(
            C=0.1, class_weight="balanced", dual=False, max_iter=5000,
            random_state=RANDOM_STATE,
        )
    elif model_name == "knn":
        return KNeighborsClassifier(n_neighbors=25, weights="distance", n_jobs=-1)
    raise ValueError(model_name)


def score_model(model, X):
    """Unified continuous score: decision_function for LinearSVC (unbounded
    margin, no predict_proba), predict_proba fail-class column for KNN."""
    if hasattr(model, "decision_function"):
        return model.decision_function(X)
    return model.predict_proba(X)[:, 1]


def best_threshold(y_true, y_score):
    """Pick the score cutoff maximizing fail-class F1. Searches over the
    score's own observed range so it works for both bounded (KNN proba, ~[0,1])
    and unbounded (SVM decision_function) scores."""
    lo, hi = float(np.min(y_score)), float(np.max(y_score))
    thresholds = np.linspace(lo, hi, 199)
    best_t, best_f1 = 0.5 * (lo + hi), -1.0
    for t in thresholds:
        f1 = f1_score(y_true, (y_score >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    return best_t, best_f1


def cross_validate(model_name, X_param, X_spatial, X_block, y, groups, stage):
    """GroupKFold(5); pool out-of-fold scores across all folds, then pick the
    threshold that maximizes fail-class F1 on the pooled OOF predictions."""
    gkf = GroupKFold(n_splits=N_SPLITS)
    oof_score = np.zeros(len(y), dtype=np.float64)

    for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_param, y, groups)):
        Xb_tr = X_block[tr_idx] if stage == "B" else None
        Xb_va = X_block[va_idx] if stage == "B" else None
        X_tr, X_va = build_representation(
            model_name,
            X_param[tr_idx], X_spatial[tr_idx], Xb_tr,
            X_param[va_idx], X_spatial[va_idx], Xb_va,
            stage,
        )
        model = make_model(model_name)
        model.fit(X_tr, y[tr_idx])
        oof_score[va_idx] = score_model(model, X_va)
        del Xb_tr, Xb_va, X_tr, X_va, model
        gc.collect()
        print(f"  [CV] model={model_name} stage={stage} fold={fold} done")

    t, f1 = best_threshold(y, oof_score)
    print(f"  [CV] model={model_name} stage={stage} OOF fail-F1={f1:.4f} threshold={t:.4f}")
    return t, f1


def evaluate(y_true, y_score, threshold):
    y_pred = (y_score >= threshold).astype(int)
    return {
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "pr_auc": average_precision_score(y_true, y_score),
        "roc_auc": roc_auc_score(y_true, y_score),
        "accuracy": accuracy_score(y_true, y_pred),
    }


def save_svm_coefficients(model, stage):
    """Bonus: pull .coef_ from the frozen LinearSVC as a free interpretability
    artifact (the team currently has none). Ranked by absolute weight so the
    most influential features -- die-level, spatial, or block-PCA -- surface
    at the top regardless of sign."""
    names = list(FEATURE_COLS) + ["spatial_neighbor_fail_density", "spatial_radial_dist"]
    if stage == "B":
        names += [f"block_pca_{i + 1}" for i in range(BLOCK_PCA_COMPONENTS)]
    coef = model.coef_.ravel()
    assert len(coef) == len(names), "coef_/name length mismatch"
    ranked = sorted(zip(names, coef.tolist()), key=lambda kv: abs(kv[1]), reverse=True)
    out_path = f"coefficients_svm_{stage}.json"
    with open(out_path, "w") as f:
        json.dump(
            {"intercept": float(model.intercept_[0]), "coefficients_by_abs_weight": ranked},
            f, indent=2,
        )
    print(f"Saved LinearSVC coefficients -> {out_path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["svm", "knn"], required=True)
    ap.add_argument("--stage", choices=["A", "B"], required=True)
    ap.add_argument("--smoke", type=int, default=None,
                     help="if set, only read this many rows (fast correctness check)")
    args = ap.parse_args()

    need_blocks = args.stage == "B"
    # Smoke runs must never share a cache_tag with a full run -- see
    # sai_nithin_features / train_sai_nithin's identical convention.
    train_tag = "train" if args.smoke is None else f"train_smoke{args.smoke}"
    test_tag = "test" if args.smoke is None else f"test_smoke{args.smoke}"

    t0 = time.time()
    train = load_split("input/train.csv", train_tag, need_blocks, nrows=args.smoke)
    test = load_split("input/test.csv", test_tag, need_blocks, nrows=args.smoke)
    load_time = time.time() - t0

    assert len(train["meta"]) == len(train["X_spatial"]) == \
        (len(train["X_block"]) if need_blocks else len(train["meta"])), \
        "train row-count mismatch between meta/spatial/block arrays -- cache corruption"
    assert len(test["meta"]) == len(test["X_spatial"]) == \
        (len(test["X_block"]) if need_blocks else len(test["meta"])), \
        "test row-count mismatch between meta/spatial/block arrays -- cache corruption"

    tr_meta, te_meta = train["meta"], test["meta"]

    # Filter to eligible dies (old_label == 0) AFTER spatial features were built
    # on the full wafer inside load_split -> build_spatial_features.
    tr_mask = (tr_meta["old_label"] == 0).to_numpy()
    te_mask = (te_meta["old_label"] == 0).to_numpy()

    X_param_tr = train["X_param"][tr_mask]
    X_spatial_tr = train["X_spatial"][tr_mask]
    X_block_tr = train["X_block"][tr_mask] if need_blocks else None
    y_tr = tr_meta["label"].to_numpy()[tr_mask]
    groups_tr = tr_meta["wafer_id"].to_numpy()[tr_mask]

    X_param_te = test["X_param"][te_mask]
    X_spatial_te = test["X_spatial"][te_mask]
    X_block_te = test["X_block"][te_mask] if need_blocks else None
    y_te = te_meta["label"].to_numpy()[te_mask]

    print(f"Loaded. train eligible={tr_mask.sum()} ({y_tr.mean()*100:.2f}% fail), "
          f"test eligible={te_mask.sum()} ({y_te.mean()*100:.2f}% fail). "
          f"load+preprocess time={load_time:.1f}s")

    t1 = time.time()
    threshold, cv_f1 = cross_validate(
        args.model, X_param_tr, X_spatial_tr, X_block_tr, y_tr, groups_tr, args.stage
    )
    cv_time = time.time() - t1

    # Refit on the FULL training set with the frozen hyperparameters/threshold,
    # score test.csv ONCE.
    t2 = time.time()
    X_tr_final, X_te_final = build_representation(
        args.model, X_param_tr, X_spatial_tr, X_block_tr,
        X_param_te, X_spatial_te, X_block_te, args.stage,
    )
    model = make_model(args.model)
    model.fit(X_tr_final, y_tr)
    train_time = time.time() - t2

    test_score = score_model(model, X_te_final)
    metrics = evaluate(y_te, test_score, threshold)
    metrics["threshold"] = threshold
    metrics["cv_oof_f1"] = cv_f1
    metrics["preprocessing_time_min"] = round(load_time / 60, 2)
    metrics["actual_training_time_min"] = round((cv_time + train_time) / 60, 2)
    metrics["total_time_min"] = round((load_time + cv_time + train_time) / 60, 2)

    print("\n=== FINAL TEST METRICS (frozen model, scored once) ===")
    print(json.dumps(metrics, indent=2))

    out_path = f"results_{args.model}_{args.stage}.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\nSaved -> {out_path}")

    if args.model == "svm":
        save_svm_coefficients(model, args.stage)


if __name__ == "__main__":
    main()
