from __future__ import annotations

from typing import Any

import lightgbm as lgb

from ..config import MODEL_DEFAULTS, RANDOM_STATE


def build_lightgbm(overrides: dict[str, Any] | None = None, device: str = "cpu") -> lgb.LGBMClassifier:
    params = dict(MODEL_DEFAULTS["A3"])
    params.update(overrides or {})
    early_stopping_rounds = int(params.pop("early_stopping_rounds", 100))
    params.setdefault("random_state", RANDOM_STATE)
    params.setdefault("n_jobs", -1)
    params.setdefault("metric", "average_precision")
    if device.lower() == "gpu":
        params.setdefault("device_type", "gpu")
    model = lgb.LGBMClassifier(**params)
    model._benchmark_early_stopping_rounds = early_stopping_rounds
    return model


def fit_lightgbm(model: lgb.LGBMClassifier, x_train, y_train, x_valid, y_valid, patience: int | None = None):
    patience = getattr(model, "_benchmark_early_stopping_rounds", 100) if patience is None else patience
    model.fit(
        x_train,
        y_train,
        eval_set=[(x_valid, y_valid)],
        eval_metric="average_precision",
        callbacks=[lgb.early_stopping(patience), lgb.log_evaluation(100)],
    )
    return model
