from __future__ import annotations

from typing import Any

from catboost import CatBoostClassifier

from ..config import MODEL_DEFAULTS, RANDOM_STATE


def build_catboost(overrides: dict[str, Any] | None = None, device: str = "gpu") -> CatBoostClassifier:
    params = dict(MODEL_DEFAULTS["A1"])
    params.update(overrides or {})
    early_stopping_rounds = int(params.pop("early_stopping_rounds", 100))
    params.setdefault("random_seed", RANDOM_STATE)
    params.setdefault("task_type", "GPU" if device.lower() == "gpu" else "CPU")
    params.setdefault("verbose", 100)
    params.setdefault("allow_writing_files", False)
    model = CatBoostClassifier(**params)
    model._benchmark_early_stopping_rounds = early_stopping_rounds
    return model


def fit_catboost(model: CatBoostClassifier, x_train, y_train, x_valid, y_valid, cat_features=None):
    model.fit(
        x_train,
        y_train,
        eval_set=(x_valid, y_valid),
        cat_features=cat_features,
        early_stopping_rounds=getattr(model, "_benchmark_early_stopping_rounds", 100),
        use_best_model=True,
    )
    return model
