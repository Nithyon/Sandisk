import numpy as np

from src.models.catboost_model import build_catboost
from src.models.lightgbm_model import build_lightgbm, fit_lightgbm
from src.models.xgboost_model import build_xgboost


def test_tree_builders_preserve_requested_gpu_semantics():
    cat = build_catboost({"iterations": 2}, device="gpu")
    xgb = build_xgboost({"n_estimators": 2}, device="gpu", scale_pos_weight=3.0)
    lgb = build_lightgbm({"n_estimators": 2}, device="cpu")
    assert cat.get_params()["task_type"] == "GPU"
    assert xgb.get_params()["device"] == "cuda"
    assert xgb.get_params()["scale_pos_weight"] == 3.0
    assert lgb.get_params()["random_state"] == 42


def test_tree_builders_preserve_early_stopping_overrides():
    cat = build_catboost({"early_stopping_rounds": 7}, device="cpu")
    xgb = build_xgboost({"early_stopping_rounds": 8}, device="cpu")
    lgb = build_lightgbm({"early_stopping_rounds": 9}, device="cpu")
    assert cat._benchmark_early_stopping_rounds == 7
    assert xgb._benchmark_early_stopping_rounds == 8
    assert lgb._benchmark_early_stopping_rounds == 9


def test_tree_builders_can_fit_tiny_cpu_data():
    rng = np.random.default_rng(42)
    x = rng.normal(size=(30, 4)).astype(np.float32)
    y = np.array([0] * 20 + [1] * 10)
    for model in (
        build_catboost({"iterations": 2, "verbose": False}, device="cpu"),
        build_xgboost({"n_estimators": 2}, device="cpu", scale_pos_weight=2.0),
        build_lightgbm({"n_estimators": 2, "verbosity": -1}, device="cpu"),
    ):
        model.fit(x, y)
        assert model.predict_proba(x)[:, 1].shape == (30,)


def test_lightgbm_early_stopping_monitors_only_average_precision():
    rng = np.random.default_rng(42)
    x = rng.normal(size=(400, 8)).astype(np.float32)
    y = (x[:, 0] + 0.4 * x[:, 1] > 1.4).astype(np.int8)
    model = build_lightgbm(
        {"n_estimators": 20, "min_child_samples": 5, "verbosity": -1},
        device="cpu",
    )

    fit_lightgbm(model, x[:300], y[:300], x[300:], y[300:], patience=5)

    assert list(model.best_score_["valid_0"]) == ["average_precision"]
