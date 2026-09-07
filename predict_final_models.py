"""Load a final Model A/B bundle and output one failure probability per die."""
from __future__ import annotations

import argparse
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from sai_nithin_features import FEATURE_COLS, build_spatial_features, parse_block_readings


def transform(values: np.ndarray, fitted: dict) -> np.ndarray:
    scaled = (values - fitted["mean"]) / fitted["std"]
    return fitted["pca"].transform(scaled).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="Path to model_A_logreg.joblib or model_B_logreg.joblib")
    parser.add_argument("--input", required=True, help="CSV with die features, old_label, and block_readings for Model B")
    parser.add_argument("--output", required=True)
    parser.add_argument("--limit", type=int, default=None, help="Optional smoke-test row limit")
    args = parser.parse_args()

    bundle = joblib.load(args.model)
    columns = ["wafer_id", "die_row", "die_col", "old_label", *FEATURE_COLS]
    if bundle["stage"] == "B":
        columns.append("block_readings")
    frame = pd.read_csv(args.input, usecols=columns, nrows=args.limit)
    spatial = build_spatial_features(frame).to_numpy(dtype=np.float32)
    param = transform(frame[FEATURE_COLS].to_numpy(dtype=np.float32), bundle["param_transform"])
    eligible = (frame["old_label"] == 0).to_numpy()
    parts = [param[eligible], spatial[eligible]]
    if bundle["stage"] == "B":
        blocks = parse_block_readings(frame.loc[eligible, "block_readings"])
        parts.append(transform(blocks, bundle["block_transform"]))
    probability = bundle["model"].predict_proba(np.hstack(parts))[:, 1]

    output = frame[["wafer_id", "die_row", "die_col", "old_label"]].copy()
    output["eligible"] = eligible
    output["failure_probability"] = 1.0
    output.loc[eligible, "failure_probability"] = probability
    output["threshold"] = bundle["threshold"]
    output["predicted_label"] = (output["failure_probability"] >= bundle["threshold"]).astype(np.int8)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.output, index=False)
    print(f"Saved {len(output):,} predictions to {args.output}")


if __name__ == "__main__":
    main()
