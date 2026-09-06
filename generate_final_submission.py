"""Train the selected PCA + Logistic Regression Model B and score validation.csv."""

import argparse
import time
from pathlib import Path

import numpy as np
import pandas as pd

from sai_nithin_features import load_split
from train_sai_nithin import build_representation, cross_validate, make_model


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate the final validation submission using PCA + Logistic Regression Model B."
    )
    parser.add_argument("--train", type=Path, default=Path("input/train.csv"))
    parser.add_argument("--validation", type=Path, default=Path("input/validation.csv"))
    parser.add_argument("--output", type=Path, default=Path("submission_pca_logreg_b.csv"))
    parser.add_argument("--smoke", type=int, default=None, help="read only N rows from each split")
    args = parser.parse_args()

    started = time.time()
    suffix = "" if args.smoke is None else f"_smoke{args.smoke}"
    train = load_split(args.train, f"submission_train{suffix}", need_blocks=True, nrows=args.smoke)
    validation = load_split(
        args.validation,
        f"submission_validation{suffix}",
        need_blocks=True,
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
    train_blocks = train["X_block"][train_eligible]
    validation_param = validation["X_param"][validation_eligible]
    validation_spatial = validation["X_spatial"][validation_eligible]
    validation_blocks = validation["X_block"][validation_eligible]

    best_components, cv_result = cross_validate(
        train_param,
        train_spatial,
        train_blocks,
        y_train,
        groups,
        stage="B",
        model_name="logreg",
    )
    x_train, x_validation = build_representation(
        train_param,
        train_spatial,
        train_blocks,
        validation_param,
        validation_spatial,
        validation_blocks,
        stage="B",
        n_components_param=best_components,
        n_components_block=cv_result["n_block"],
    )
    model = make_model("logreg")
    model.fit(x_train, y_train)
    eligible_probabilities = model.predict_proba(x_validation)[:, 1]
    submission = build_submission(validation_meta, eligible_probabilities, cv_result["threshold"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    submission.to_csv(args.output, index=False)
    print(
        f"Saved {len(submission):,} predictions to {args.output} "
        f"(PCA={best_components}, block PCA={cv_result['n_block']}, "
        f"threshold={cv_result['threshold']:.3f}, elapsed={time.time() - started:.1f}s)"
    )


if __name__ == "__main__":
    main()
