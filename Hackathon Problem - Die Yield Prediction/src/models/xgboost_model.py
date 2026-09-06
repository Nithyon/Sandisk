from __future__ import annotations

from typing import Any

from xgboost import XGBClassifier

from ..config import MODEL_DEFAULTS, RANDOM_STATE


def build_xgboost(
    overrides: dict[str, Any] | None = None, device: str = "gpu", scale_pos_weight: float = 1.0
) -> XGBClassifier:
    params = dict(MODEL_DEFAULTS["A2"])
    params.update(overrides or {})
    early_stopping_rounds = int(params.pop("early_stopping_rounds", 100))
    params.setdefault("random_state", RANDOM_STATE)
    params.setdefault("device", "cuda" if device.lower() == "gpu" else "cpu")
    params.setdefault("scale_pos_weight", float(scale_pos_weight))
    params.setdefault("n_jobs", -1)
    model = XGBClassifier(**params)
    model._benchmark_early_stopping_rounds = early_stopping_rounds
    return model


def fit_xgboost(model: XGBClassifier, x_train, y_train, x_valid, y_valid):
    model.set_params(early_stopping_rounds=getattr(model, "_benchmark_early_stopping_rounds", 100))
    model.fit(x_train, y_train, eval_set=[(x_valid, y_valid)], verbose=False)
    return model
