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
import time
import json
import numpy as np
from sklearn.model_selection import GroupKFold
from sklearn.preprocessing import StandardScaler
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


def build_representation(X_param_train, X_spatial_train, X_block_train,
                          X_param_eval, X_spatial_eval, X_block_eval,
                          stage, n_components_param, n_components_block=None):
    """
    Fit scaler+PCA on the TRAIN portion only, transform both train and eval.
    Model A: PCA(param) + spatial.
    Model B: PCA(param) + spatial + PCA(block)  [separately fitted PCA on blocks]
    """
    param_scaler = StandardScaler().fit(X_param_train)
    param_pca = PCA(n_components=n_components_param, random_state=RANDOM_STATE)
    param_pca.fit(param_scaler.transform(X_param_train))

    train_parts = [param_pca.transform(param_scaler.transform(X_param_train)), X_spatial_train]
    eval_parts = [param_pca.transform(param_scaler.transform(X_param_eval)), X_spatial_eval]

    if stage == "B":
        block_scaler = StandardScaler().fit(X_block_train)
        block_pca = PCA(n_components=n_components_block, random_state=RANDOM_STATE)
        block_pca.fit(block_scaler.transform(X_block_train))
        train_parts.append(block_pca.transform(block_scaler.transform(X_block_train)))
        eval_parts.append(block_pca.transform(block_scaler.transform(X_block_eval)))

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

        t, f1 = best_threshold(y, oof_prob)
        results_by_ncomp[n_param] = {"threshold": t, "f1": f1, "n_block": n_block}
        print(f"  [CV] stage={stage} n_param={n_param} n_block={n_block} "
              f"OOF fail-F1={f1:.4f} threshold={t:.3f}")

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

    t0 = time.time()
    train = load_split("input/train.csv", "train", need_blocks, nrows=args.smoke)
    test = load_split("input/test.csv", "test", need_blocks, nrows=args.smoke)
    load_time = time.time() - t0

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
