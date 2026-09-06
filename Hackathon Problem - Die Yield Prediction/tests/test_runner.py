import numpy as np
import pandas as pd

from src.config import ProjectPaths
from src.runner import EXPERIMENT_REGISTRY, experiment_family, model_a_matrix, positive_weight, run_cross_validation, run_tag


def test_registry_covers_the_requested_grid():
    expected = {"A1", "A2", "A3", "A4", "B1", "B2", "AE32", "AE64", "B3a", "B3b", "B4", "B5", "B6"}
    assert expected == set(EXPERIMENT_REGISTRY)
    assert experiment_family("A1") == "catboost"
    assert experiment_family("B5") == "cnn"


def test_model_a_matrix_filters_eligibility_without_dropping_spatial_inputs():
    frame = pd.DataFrame(
        {
            "wafer_id": ["W1", "W1", "W2"],
            "die_row": [0.0, 1.0, 0.0],
            "feature_1": [1.0, 2.0, 3.0],
            "radial_distance": [0.1, 0.2, 0.3],
            "old_label": [1, 0, 0],
            "label": [1, 1, 0],
        }
    )
    x, y, groups, row_index, columns = model_a_matrix(frame)
    assert x.dtype == np.float32
    assert x.shape == (2, 3)
    assert columns == ["die_row", "feature_1", "radial_distance"]
    assert y.tolist() == [1, 0]
    assert groups.tolist() == ["W1", "W2"]
    assert row_index.tolist() == [1, 2]
    assert positive_weight(y) == 1.0


def test_run_tag_prevents_parameter_variants_overwriting_predictions():
    assert run_tag("B2", {"pca_components": 50}) != run_tag("B2", {"pca_components": 100})
    assert run_tag("A1", {}) == "A1_default"
    assert run_tag("A1", {}, n_splits=5, device="gpu") != run_tag("A1", {}, n_splits=3, device="gpu")
    assert run_tag("A1", {}, n_splits=5, device="gpu") != run_tag("A1", {}, n_splits=5, device="cpu")


def test_outer_validation_rows_are_not_used_for_supervised_early_stopping(tmp_path, monkeypatch):
    paths = ProjectPaths(tmp_path)
    train_cache = paths.cache_dir / "train"
    train_cache.mkdir(parents=True)
    rows = []
    for wafer in range(10):
        for die in range(4):
            rows.append(
                {
                    "wafer_id": f"W{wafer}",
                    "die_row": float(die),
                    "die_col": float(wafer % 2),
                    "feature_1": float(wafer * 10 + die),
                    "old_label": 0,
                    "label": int(die == 3),
                }
            )
    pd.DataFrame(rows).to_parquet(train_cache / "model_a.parquet", index=False)
    np.save(train_cache / "blocks.float32.npy", np.zeros((40, 8), dtype=np.float32))
    calls = []

    def fake_fit_tree(experiment_id, x_fit, y_fit, x_stop, y_stop, x_predict, columns, **kwargs):
        feature = columns.index("feature_1")
        fit_rows = set(x_fit[:, feature])
        stop_rows = set(x_stop[:, feature])
        predict_rows = set(x_predict[:, feature])
        assert fit_rows.isdisjoint(stop_rows)
        assert fit_rows.isdisjoint(predict_rows)
        assert stop_rows.isdisjoint(predict_rows)
        calls.append((len(x_fit), len(x_stop), len(x_predict)))
        return object(), np.full(len(x_predict), 0.25, dtype=np.float32)

    monkeypatch.setattr("src.runner._fit_tree", fake_fit_tree)
    run_cross_validation("A1", paths, device="cpu", n_splits=2)
    assert len(calls) == 2


def test_a1_and_a2_complete_grouped_cv_on_a_tiny_cache(tmp_path):
    paths = ProjectPaths(tmp_path)
    train_cache = paths.cache_dir / "train"
    train_cache.mkdir(parents=True)
    rows = []
    for wafer in range(10):
        for die in range(4):
            rows.append(
                {
                    "wafer_id": f"W{wafer}",
                    "die_row": float(die),
                    "die_col": float(wafer % 2),
                    "radial_distance": die / 3,
                    "zone_id": die,
                    "feature_1": float(wafer + die),
                    "old_label": 0,
                    "label": int(die == 3),
                }
            )
    pd.DataFrame(rows).to_parquet(train_cache / "model_a.parquet", index=False)
    np.save(
        train_cache / "blocks.float32.npy",
        np.random.default_rng(42).normal(size=(40, 8)).astype(np.float32),
    )
    a1 = run_cross_validation("A1", paths, device="cpu", overrides={"iterations": 2, "verbose": False}, n_splits=2)
    a2 = run_cross_validation("A2", paths, device="cpu", overrides={"n_estimators": 2}, n_splits=2)
    b1 = run_cross_validation("B1", paths, device="cpu", overrides={"iterations": 2, "verbose": False}, n_splits=2)
    b2 = run_cross_validation("B2", paths, device="cpu", overrides={"n_estimators": 2, "pca_components": 2}, n_splits=2)
    assert 0 <= a1["fail_f1"] <= 1
    assert 0 <= a2["fail_f1"] <= 1
    assert 0 <= b1["fail_f1"] <= 1
    assert 0 <= b2["fail_f1"] <= 1
    assert len(list(paths.predictions_dir.glob("*_oof.csv"))) == 4
