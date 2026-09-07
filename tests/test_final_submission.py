import numpy as np
import pandas as pd
import joblib

from generate_final_submission import build_submission, fit_model_bundle, predict_probabilities
from sai_nithin_features import FEATURE_COLS, load_split
import train_sai_nithin


def test_build_submission_forces_old_failures_and_preserves_required_schema():
    metadata = pd.DataFrame(
        {
            "wafer_id": ["W1", "W1", "W2"],
            "die_row": [0, 0, 1],
            "die_col": [0, 1, 0],
            "old_label": [1, 0, 0],
        }
    )

    submission = build_submission(metadata, np.array([0.89, 0.91]), threshold=0.90)

    assert submission.columns.tolist() == ["wafer_id", "die_row", "die_col", "predicted_label"]
    assert submission["predicted_label"].tolist() == [1, 0, 1]


def test_load_split_accepts_unlabeled_validation_csv(tmp_path, monkeypatch):
    monkeypatch.setattr("sai_nithin_features.CACHE_DIR", str(tmp_path / "cache"))
    row = {
        "wafer_id": "W1",
        "die_row": 0,
        "die_col": 0,
        "old_label": 0,
        **{column: 0.0 for column in FEATURE_COLS},
    }
    path = tmp_path / "validation.csv"
    pd.DataFrame([row]).to_csv(path, index=False)

    loaded = load_split(path, "validation_test", need_blocks=False, require_label=False)

    assert loaded["meta"].columns.tolist() == ["wafer_id", "die_row", "die_col", "old_label"]
    assert loaded["X_param"].shape == (1, 500)


def test_load_split_does_not_reuse_cache_for_a_different_csv(tmp_path, monkeypatch):
    monkeypatch.setattr("sai_nithin_features.CACHE_DIR", str(tmp_path / "cache"))

    def write_split(path, old_labels, block_value):
        rows = []
        block = " ".join([str(block_value)] * 2000)
        for index, old_label in enumerate(old_labels):
            rows.append(
                {
                    "wafer_id": "W1",
                    "die_row": index // 2,
                    "die_col": index % 2,
                    "old_label": old_label,
                    "block_readings": block,
                    **{column: 0.0 for column in FEATURE_COLS},
                }
            )
        pd.DataFrame(rows).to_csv(path, index=False)

    first_path = tmp_path / "first.csv"
    second_path = tmp_path / "second.csv"
    write_split(first_path, [1, 0, 0, 0], 1.0)
    write_split(second_path, [0, 0, 0, 0], 2.0)

    first = load_split(first_path, "shared", need_blocks=True, require_label=False)
    second = load_split(second_path, "shared", need_blocks=True, require_label=False)

    assert not np.array_equal(first["X_spatial"], second["X_spatial"])
    assert not np.array_equal(first["X_block"], second["X_block"])


def test_model_bundles_preserve_distinct_a_and_b_representations(tmp_path):
    rng = np.random.default_rng(42)
    x_param = rng.normal(size=(12, 4)).astype(np.float32)
    x_spatial = rng.normal(size=(12, 2)).astype(np.float32)
    x_block = rng.normal(size=(12, 6)).astype(np.float32)
    y = np.array([0, 1] * 6, dtype=np.int8)

    model_a = fit_model_bundle("A", x_param, x_spatial, None, y, 2, None, 0.6)
    model_b = fit_model_bundle("B", x_param, x_spatial, x_block, y, 2, 2, 0.7)

    assert model_a["stage"] == "A"
    assert model_a["block_pca"] is None
    assert model_b["stage"] == "B"
    assert model_b["block_pca"] is not None
    assert model_a["threshold"] == 0.6
    assert model_b["threshold"] == 0.7

    artifact = tmp_path / "model_b.joblib"
    joblib.dump(model_b, artifact)
    restored = joblib.load(artifact)
    expected = predict_probabilities(model_b, x_param, x_spatial, x_block)
    actual = predict_probabilities(restored, x_param, x_spatial, x_block)
    np.testing.assert_allclose(actual, expected)


def test_cross_validation_returns_complete_benchmark_metrics(monkeypatch):
    monkeypatch.setattr(train_sai_nithin, "PCA_CANDIDATES", [1])
    rng = np.random.default_rng(7)
    x_param = rng.normal(size=(20, 3)).astype(np.float32)
    x_spatial = rng.normal(size=(20, 2)).astype(np.float32)
    y = np.tile([0, 1], 10).astype(np.int8)
    groups = np.repeat(np.arange(10), 2)

    _, result = train_sai_nithin.cross_validate(
        x_param, x_spatial, None, y, groups, stage="A", model_name="logreg"
    )

    assert {"f1", "precision", "recall", "pr_auc", "roc_auc", "accuracy", "threshold"} <= result.keys()
