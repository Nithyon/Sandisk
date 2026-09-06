import numpy as np
import pandas as pd
import time

from src.evaluation import (
    append_benchmark_result,
    classification_metrics,
    group_folds,
    select_f1_threshold,
)


def test_group_folds_never_mix_wafers():
    groups = np.repeat([f"W{i}" for i in range(10)], 2)
    y = np.tile([0, 1], 10)
    for train_idx, valid_idx in group_folds(y, groups, n_splits=5):
        assert set(groups[train_idx]).isdisjoint(groups[valid_idx])


def test_threshold_maximizes_fail_f1_with_deterministic_tie_break():
    y = np.array([0, 0, 1, 1])
    probability = np.array([0.1, 0.4, 0.35, 0.8])
    threshold = select_f1_threshold(y, probability)
    assert threshold == 0.35


def test_threshold_selection_scales_to_out_of_fold_prediction_volume():
    rng = np.random.default_rng(42)
    y = rng.integers(0, 2, size=10_000, dtype=np.int8)
    probability = rng.random(10_000)
    started = time.perf_counter()
    threshold = select_f1_threshold(y, probability)
    assert 0.0 <= threshold <= 1.0
    assert time.perf_counter() - started < 1.0


def test_metrics_use_failure_as_positive_class():
    metrics = classification_metrics(
        np.array([0, 0, 1, 1]), np.array([0.1, 0.8, 0.7, 0.9]), 0.5
    )
    assert metrics["fail_f1"] == 0.8
    assert metrics["precision"] == 2 / 3
    assert metrics["recall"] == 1.0
    assert metrics["accuracy"] == 0.75


def test_results_are_written_only_when_metrics_are_complete(tmp_path):
    path = tmp_path / "benchmark_results.csv"
    row = {
        "experiment_id": "A1",
        "model_name": "CatBoost",
        "model_variant": "cv",
        "fail_f1": 0.4,
        "precision": 0.5,
        "recall": 1 / 3,
        "pr_auc": 0.3,
        "roc_auc": 0.7,
        "accuracy": 0.9,
        "threshold": 0.2,
        "preprocessing_time": 1.0,
        "training_time": 2.0,
        "total_time": 3.0,
        "notes": "measured",
    }
    append_benchmark_result(path, row)
    saved = pd.read_csv(path)
    assert saved.loc[0, "experiment_id"] == "A1"
