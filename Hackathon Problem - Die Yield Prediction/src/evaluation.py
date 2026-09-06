from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator, Mapping

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, average_precision_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import GroupKFold


RESULT_COLUMNS = [
    "experiment_id", "model_name", "model_variant", "fail_f1", "precision", "recall",
    "pr_auc", "roc_auc", "accuracy", "threshold", "preprocessing_time", "training_time", "total_time", "notes",
]


def group_folds(y: np.ndarray, groups: np.ndarray, n_splits: int = 5) -> Iterator[tuple[np.ndarray, np.ndarray]]:
    groups = np.asarray(groups)
    if np.unique(groups).size < n_splits:
        raise ValueError(f"need at least {n_splits} unique wafers")
    splitter = GroupKFold(n_splits=n_splits)
    for train_idx, valid_idx in splitter.split(np.zeros(len(y)), y, groups):
        if set(groups[train_idx]).intersection(groups[valid_idx]):
            raise RuntimeError("wafer leakage detected")
        yield train_idx, valid_idx


def select_f1_threshold(y_true: np.ndarray, probability: np.ndarray) -> float:
    y_true = np.asarray(y_true, dtype=np.int8)
    probability = np.asarray(probability, dtype=np.float64)
    if len(y_true) == 0 or len(y_true) != len(probability):
        raise ValueError("targets and probabilities must be non-empty and aligned")
    if not np.isfinite(probability).all():
        raise ValueError("probabilities must be finite")
    order = np.argsort(-probability, kind="stable")
    sorted_probability = probability[order]
    sorted_target = y_true[order]
    cumulative_true_positive = np.cumsum(sorted_target, dtype=np.int64)
    group_ends = np.flatnonzero(np.r_[sorted_probability[1:] != sorted_probability[:-1], True])
    true_positive = cumulative_true_positive[group_ends].astype(np.float64)
    predicted_positive = (group_ends + 1).astype(np.float64)
    actual_positive = float(sorted_target.sum())
    precision = np.divide(true_positive, predicted_positive, out=np.zeros_like(true_positive), where=predicted_positive > 0)
    f1 = np.divide(
        2.0 * true_positive,
        actual_positive + predicted_positive,
        out=np.zeros_like(true_positive),
        where=(actual_positive + predicted_positive) > 0,
    )
    thresholds = sorted_probability[group_ends]
    best = np.lexsort((thresholds, -precision, -f1))[0]
    return float(thresholds[best])


def classification_metrics(y_true: np.ndarray, probability: np.ndarray, threshold: float) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=np.int8)
    probability = np.asarray(probability, dtype=np.float64)
    predicted = probability >= threshold
    return {
        "fail_f1": float(f1_score(y_true, predicted, zero_division=0)),
        "precision": float(precision_score(y_true, predicted, zero_division=0)),
        "recall": float(recall_score(y_true, predicted, zero_division=0)),
        "pr_auc": float(average_precision_score(y_true, probability)),
        "roc_auc": float(roc_auc_score(y_true, probability)) if np.unique(y_true).size == 2 else float("nan"),
        "accuracy": float(accuracy_score(y_true, predicted)),
        "threshold": float(threshold),
    }


def append_benchmark_result(path: Path, row: Mapping[str, object]) -> None:
    missing = [column for column in RESULT_COLUMNS if column not in row or row[column] is None]
    if missing:
        raise ValueError(f"refusing incomplete benchmark row; missing {missing}")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=RESULT_COLUMNS)
    updated = pd.concat([existing, pd.DataFrame([{column: row[column] for column in RESULT_COLUMNS}])], ignore_index=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    updated.to_csv(temporary, index=False)
    os.replace(temporary, path)
