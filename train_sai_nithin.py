"""
Sai Nithin's experiment lane: PCA + Logistic Regression, PCA + XGBoost
-- Model A (die-level + spatial) and Model B (+ PCA(block readings)).

Follows the tracker's shared rules (Google Sheet "Copy of Die Yield Model
Comparison Tracker") and the settled PCA definition from COORDINATION_LOG.md:

  - Evaluate only dies with old_label == 0 (eligible dies).
  - Spatial features built on the FULL wafer, filtered to old_label == 0 after.
  - GroupKFold(5) on wafer_id, random_state=42.
  - class_weight="balanced" (LogReg) / scale_pos_weight=22.6 (XGBoost).
  - Threshold chosen from out-of-fold validation predictions, not blind 0.5.
  - Score test.csv ONCE with the model frozen.
  - Report Precision/Recall/F1/PR-AUC/ROC-AUC on the FAIL class (label=1),
    Accuracy overall.

Usage:
    venv\\Scripts\\python.exe train_sai_nithin.py --model logreg --stage A
    venv\\Scripts\\python.exe train_sai_nithin.py --model xgboost --stage B
    venv\\Scripts\\python.exe train_sai_nithin.py --model logreg --stage A --smoke 5000
"""
import argparse
import gc
import time
import json
import numpy as np
from sklearn.model_selection import GroupKFold
from sklearn.decomposition import PCA
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    f1_score, precision_score, recall_score,
    average_precision_score, roc_auc_score, accuracy_score,
)

from sai_nithin_features import load_split

SCALE_POS_WEIGHT = 22.6
N_SPLITS = 5
RANDOM_STATE = 42
PCA_CANDIDATES = [50, 100, 150]  # candidate component counts, picked by val fail-F1


def _standardize_f32(X_train, X_eval):
    """
    Lean float32 standardization (fit on train only), used instead of
    sklearn's StandardScaler. StandardScaler upcasts to float64 internally
    (see _incremental_mean_and_var), which on the 2000-column block matrix
    doubles peak memory and can OOM on a machine with only a few GB free.
    Keeping everything float32 halves that peak.
    """
    mean = X_train.mean(axis=0, dtype=np.float32)
    std = X_train.std(axis=0, dtype=np.float32)
    std[std == 0] = 1.0
    return (X_train - mean) / std, (X_eval - mean) / std


def _pca_transform(X_train, X_eval, n_components, wide):
    """Fit PCA on train only, transform both. `wide=True` (the 2000-column
    block branch) uses randomized SVD, which is far cheaper in memory and
    time than full SVD when extracting few components from many columns."""
    pca = PCA(
        n_components=n_components,
        svd_solver="randomized" if wide else "auto",
        random_state=RANDOM_STATE,
    )
    train_t = pca.fit_transform(X_train).astype(np.float32)
    eval_t = pca.transform(X_eval).astype(np.float32)
    return train_t, eval_t


def build_representation(X_param_train, X_spatial_train, X_block_train,
                          X_param_eval, X_spatial_eval, X_block_eval,
                          stage, n_components_param, n_components_block=None):
    """
    Fit scaler+PCA on the TRAIN portion only, transform both train and eval.
    Model A: PCA(param) + spatial.
    Model B: PCA(param) + spatial + PCA(block)  [separately fitted PCA on blocks]
    """
    param_train_s, param_eval_s = _standardize_f32(X_param_train, X_param_eval)
    param_train_pca, param_eval_pca = _pca_transform(
        param_train_s, param_eval_s, n_components_param, wide=False
    )
    del param_train_s, param_eval_s

    train_parts = [param_train_pca, X_spatial_train]
    eval_parts = [param_eval_pca, X_spatial_eval]

    if stage == "B":
        block_train_s, block_eval_s = _standardize_f32(X_block_train, X_block_eval)
        block_train_pca, block_eval_pca = _pca_transform(
            block_train_s, block_eval_s, n_components_block, wide=True
        )
        del block_train_s, block_eval_s
        train_parts.append(block_train_pca)
        eval_parts.append(block_eval_pca)
        gc.collect()

    return np.hstack(train_parts), np.hstack(eval_parts)


def make_model(model_name):
    if model_name == "logreg":
        return LogisticRegression(
            class_weight="balanced", max_iter=2000, random_state=RANDOM_STATE
        )
    elif model_name == "xgboost":
        from xgboost import XGBClassifier
        return XGBClassifier(
            scale_pos_weight=SCALE_POS_WEIGHT,
            tree_method="hist",
            device="cuda",
            n_estimators=300,
            max_depth=6,
            learning_rate=0.1,
            eval_metric="aucpr",
            random_state=RANDOM_STATE,
        )
    raise ValueError(model_name)


def best_threshold(y_true, y_prob):
    """Pick the probability cutoff maximizing fail-class F1 on given predictions."""
    thresholds = np.linspace(0.01, 0.99, 99)
    best_t, best_f1 = 0.5, -1.0
    for t in thresholds:
        f1 = f1_score(y_true, (y_prob >= t).astype(int), zero_division=0)
        if f1 > best_f1:
            best_f1, best_t = f1, t
    return best_t, best_f1


def cross_validate(X_param, X_spatial, X_block, y, groups, stage, model_name):
    """
    Run GroupKFold(5). For each candidate PCA component count, collect
    out-of-fold predicted probabilities, then pick (n_components, threshold)
    that maximizes fail-class F1 across all OOF predictions pooled together.
    """
    gkf = GroupKFold(n_splits=N_SPLITS)
    results_by_ncomp = {}

    for n_param in PCA_CANDIDATES:
        n_block = min(n_param, 100) if stage == "B" else None
        oof_prob = np.zeros(len(y), dtype=np.float64)

        for fold, (tr_idx, va_idx) in enumerate(gkf.split(X_param, y, groups)):
            Xb_tr = X_block[tr_idx] if stage == "B" else None
            Xb_va = X_block[va_idx] if stage == "B" else None
            X_tr, X_va = build_representation(
                X_param[tr_idx], X_spatial[tr_idx], Xb_tr,
                X_param[va_idx], X_spatial[va_idx], Xb_va,
                stage, n_param, n_block,
            )
            model = make_model(model_name)
            model.fit(X_tr, y[tr_idx])
            oof_prob[va_idx] = model.predict_proba(X_va)[:, 1]
            del Xb_tr, Xb_va, X_tr, X_va, model
            gc.collect()

        t, f1 = best_threshold(y, oof_prob)
        metrics = evaluate(y, oof_prob, t)
        metrics.update({"threshold": t, "n_block": n_block})
        results_by_ncomp[n_param] = metrics
        print(f"  [CV] stage={stage} n_param={n_param} n_block={n_block} "
              f"OOF fail-F1={metrics['f1']:.4f} threshold={t:.3f}")

    best_n = max(results_by_ncomp, key=lambda k: results_by_ncomp[k]["f1"])
    return best_n, results_by_ncomp[best_n]


def evaluate(y_true, y_prob, threshold):
    y_pred = (y_prob >= threshold).astype(int)
    return {
        "f1": f1_score(y_true, y_pred, zero_division=0),
        "precision": precision_score(y_true, y_pred, zero_division=0),
        "recall": recall_score(y_true, y_pred, zero_division=0),
        "pr_auc": average_precision_score(y_true, y_prob),
        "roc_auc": roc_auc_score(y_true, y_prob),
        "accuracy": accuracy_score(y_true, y_pred),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["logreg", "xgboost"], required=True)
    ap.add_argument("--stage", choices=["A", "B"], required=True)
    ap.add_argument("--smoke", type=int, default=None,
                     help="if set, only read this many rows (fast correctness check)")
    args = ap.parse_args()

    need_blocks = args.stage == "B"
    # Smoke runs (--smoke N) must never read or write the same cache files as a
    # real full-dataset run -- a shared cache_tag would let a stale N-row cache
    # silently satisfy a full run's `os.path.exists` check (or vice versa).
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
    best_n, cv_result = cross_validate(
        X_param_tr, X_spatial_tr, X_block_tr, y_tr, groups_tr, args.stage, args.model
    )
    cv_time = time.time() - t1
    print(f"Best n_param={best_n} (n_block={cv_result['n_block']}), "
          f"CV fail-F1={cv_result['f1']:.4f}, threshold={cv_result['threshold']:.3f}, "
          f"CV wall time={cv_time:.1f}s")

    # Refit on the FULL training set with the chosen config, score test ONCE.
    t2 = time.time()
    X_tr_final, X_te_final = build_representation(
        X_param_tr, X_spatial_tr, X_block_tr,
        X_param_te, X_spatial_te, X_block_te,
        args.stage, best_n, cv_result["n_block"],
    )
    model = make_model(args.model)
    model.fit(X_tr_final, y_tr)
    train_time = time.time() - t2

    test_prob = model.predict_proba(X_te_final)[:, 1]
    metrics = evaluate(y_te, test_prob, cv_result["threshold"])
    metrics["threshold"] = cv_result["threshold"]
    metrics["n_components_param"] = best_n
    metrics["n_components_block"] = cv_result["n_block"]
    metrics["preprocessing_time_min"] = round(load_time / 60, 2)
    metrics["actual_training_time_min"] = round(train_time / 60, 2)

    print("\n=== FINAL TEST METRICS (frozen model, scored once) ===")
    print(json.dumps(metrics, indent=2))

    out_path = f"results_{args.model}_{args.stage}.json"
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)
    print(f"\nSaved -> {out_path}")


if __name__ == "__main__":
    main()
