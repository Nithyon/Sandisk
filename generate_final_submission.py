"""Benchmark PCA + Logistic Regression Models A/B and score validation.csv."""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from sklearn.decomposition import PCA

from sai_nithin_features import load_split
from train_sai_nithin import RANDOM_STATE, cross_validate, make_model


def build_submission(metadata: pd.DataFrame, eligible_probabilities: np.ndarray, threshold: float) -> pd.DataFrame:
    """Return the required four-column submission, forcing pre-test failures to fail."""
    eligible = metadata["old_label"].eq(0).to_numpy()
    probabilities = np.asarray(eligible_probabilities, dtype=np.float64)
    if len(probabilities) != int(eligible.sum()):
        raise ValueError("eligible probability count does not match validation metadata")
    predicted = np.ones(len(metadata), dtype=np.int8)
    predicted[eligible] = (probabilities >= threshold).astype(np.int8)
    return pd.DataFrame(
        {
            "wafer_id": metadata["wafer_id"].to_numpy(),
            "die_row": metadata["die_row"].to_numpy(),
            "die_col": metadata["die_col"].to_numpy(),
            "predicted_label": predicted,
        }
    )


def _fit_pca_branch(values: np.ndarray, n_components: int, wide: bool):
    mean = values.mean(axis=0, dtype=np.float32)
    std = values.std(axis=0, dtype=np.float32)
    std[std == 0] = 1.0
    scaled = (values - mean) / std
    pca = PCA(
        n_components=n_components,
        svd_solver="randomized" if wide else "auto",
        random_state=RANDOM_STATE,
    )
    transformed = pca.fit_transform(scaled).astype(np.float32)
    return mean, std, pca, transformed


def fit_model_bundle(stage, x_param, x_spatial, x_block, y, n_param, n_block, threshold):
    """Fit reusable preprocessing and classifier objects for one representation."""
    param_mean, param_std, param_pca, param_t = _fit_pca_branch(x_param, n_param, wide=False)
    parts = [param_t, x_spatial]
    block_mean = block_std = block_pca = None
    if stage == "B":
        if x_block is None or n_block is None:
            raise ValueError("Model B requires block readings and block PCA components")
        block_mean, block_std, block_pca, block_t = _fit_pca_branch(x_block, n_block, wide=True)
        parts.append(block_t)
    classifier = make_model("logreg")
    classifier.fit(np.hstack(parts), y)
    return {
        "format_version": 1,
        "stage": stage,
        "threshold": float(threshold),
        "param_mean": param_mean,
        "param_std": param_std,
        "param_pca": param_pca,
        "block_mean": block_mean,
        "block_std": block_std,
        "block_pca": block_pca,
        "classifier": classifier,
    }


def predict_probabilities(bundle, x_param, x_spatial, x_block=None):
    param_scaled = (x_param - bundle["param_mean"]) / bundle["param_std"]
    parts = [bundle["param_pca"].transform(param_scaled).astype(np.float32), x_spatial]
    if bundle["stage"] == "B":
        if x_block is None:
            raise ValueError("Model B prediction requires block readings")
        block_scaled = (x_block - bundle["block_mean"]) / bundle["block_std"]
        parts.append(bundle["block_pca"].transform(block_scaled).astype(np.float32))
    return bundle["classifier"].predict_proba(np.hstack(parts))[:, 1]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Benchmark and generate validation submissions for Models A and B."
    )
    parser.add_argument("--train", type=Path, default=Path("input/train.csv"))
    parser.add_argument("--validation", type=Path, default=Path("input/validation.csv"))
    parser.add_argument("--stages", nargs="+", choices=["A", "B"], default=["A", "B"])
    parser.add_argument("--output-dir", type=Path, default=Path("."))
    parser.add_argument("--model-dir", type=Path, default=Path("models"))
    parser.add_argument("--metrics", type=Path, default=Path("benchmark_final_ab.csv"))
    parser.add_argument("--smoke", type=int, default=None, help="read only N rows from each split")
    args = parser.parse_args()

    started = time.time()
    suffix = "" if args.smoke is None else f"_smoke{args.smoke}"
    need_blocks = "B" in args.stages
    train = load_split(args.train, f"submission_train{suffix}", need_blocks=need_blocks, nrows=args.smoke)
    validation = load_split(
        args.validation,
        f"submission_validation{suffix}",
        need_blocks=need_blocks,
        nrows=args.smoke,
        require_label=False,
    )

    train_meta = train["meta"]
    validation_meta = validation["meta"]
    train_eligible = train_meta["old_label"].eq(0).to_numpy()
    validation_eligible = validation_meta["old_label"].eq(0).to_numpy()

    y_train = train_meta.loc[train_eligible, "label"].to_numpy(dtype=np.int8)
    groups = train_meta.loc[train_eligible, "wafer_id"].to_numpy()
    train_param = train["X_param"][train_eligible]
    train_spatial = train["X_spatial"][train_eligible]
    train_blocks = train["X_block"][train_eligible] if need_blocks else None
    validation_param = validation["X_param"][validation_eligible]
    validation_spatial = validation["X_spatial"][validation_eligible]
    validation_blocks = validation["X_block"][validation_eligible] if need_blocks else None

    args.output_dir.mkdir(parents=True, exist_ok=True)
    args.model_dir.mkdir(parents=True, exist_ok=True)
    metric_rows = []
    for stage in args.stages:
        stage_started = time.time()
        best_components, cv_result = cross_validate(
            train_param, train_spatial, train_blocks, y_train, groups,
            stage=stage, model_name="logreg",
        )
        bundle = fit_model_bundle(
            stage, train_param, train_spatial,
            train_blocks if stage == "B" else None,
            y_train, best_components, cv_result["n_block"], cv_result["threshold"],
        )
        probabilities = predict_probabilities(
            bundle, validation_param, validation_spatial,
            validation_blocks if stage == "B" else None,
        )
        submission = build_submission(validation_meta, probabilities, bundle["threshold"])
        submission_path = args.output_dir / f"submission_pca_logreg_{stage.lower()}.csv"
        model_path = args.model_dir / f"pca_logreg_model_{stage.lower()}.joblib"
        submission.to_csv(submission_path, index=False)
        joblib.dump(bundle, model_path, compress=3)
        metric_rows.append(
            {
                "stage": stage,
                "model_name": "PCA + Logistic Regression",
                "fail_f1": cv_result["f1"],
                "precision": cv_result["precision"],
                "recall": cv_result["recall"],
                "pr_auc": cv_result["pr_auc"],
                "roc_auc": cv_result["roc_auc"],
                "accuracy": cv_result["accuracy"],
                "threshold": cv_result["threshold"],
                "param_pca_components": best_components,
                "block_pca_components": cv_result["n_block"],
                "runtime_seconds": time.time() - stage_started,
                "submission": str(submission_path),
                "model_artifact": str(model_path),
            }
        )
        print(
            f"Model {stage}: OOF fail-F1={cv_result['f1']:.4f}, "
            f"threshold={cv_result['threshold']:.3f}, saved {submission_path} and {model_path}"
        )

    pd.DataFrame(metric_rows).to_csv(args.metrics, index=False)
    print(f"Saved benchmark comparison to {args.metrics} (elapsed={time.time() - started:.1f}s)")


if __name__ == "__main__":
    main()
