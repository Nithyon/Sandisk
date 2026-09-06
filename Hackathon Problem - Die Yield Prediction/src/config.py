from __future__ import annotations

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


RANDOM_STATE = 42
PARAMETRIC_FEATURE_COUNT = 500
BLOCK_WIDTH = 2000
EXPERIMENT_IDS = frozenset(
    {"A1", "A2", "A3", "A4", "B1", "B2", "AE32", "AE64", "B3a", "B3b", "B4", "B5", "B6"}
)


@dataclass(frozen=True)
class ProjectPaths:
    root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "root", Path(self.root).resolve())

    @property
    def input_dir(self) -> Path:
        return self.root / "input"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def results_dir(self) -> Path:
        return self.root / "results"

    @property
    def predictions_dir(self) -> Path:
        return self.results_dir / "predictions"

    @property
    def models_dir(self) -> Path:
        return self.results_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.results_dir / "logs"

    def ensure_output_dirs(self) -> None:
        for path in (self.cache_dir, self.results_dir, self.predictions_dir, self.models_dir, self.logs_dir):
            path.mkdir(parents=True, exist_ok=True)


MODEL_DEFAULTS: dict[str, dict[str, Any]] = {
    "A1": {
        "iterations": 2000,
        "learning_rate": 0.05,
        "depth": 6,
        "l2_leaf_reg": 3,
        "auto_class_weights": "Balanced",
        "eval_metric": "PRAUC",
        "early_stopping_rounds": 100,
    },
    "A2": {
        "n_estimators": 2000,
        "learning_rate": 0.05,
        "max_depth": 6,
        "subsample": 0.8,
        "colsample_bytree": 0.6,
        "min_child_weight": 5,
        "eval_metric": "aucpr",
        "tree_method": "hist",
        "early_stopping_rounds": 100,
    },
    "A3": {
        "n_estimators": 3000,
        "learning_rate": 0.05,
        "num_leaves": 63,
        "max_depth": -1,
        "min_child_samples": 50,
        "feature_fraction": 0.6,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "is_unbalance": True,
    },
}


def set_global_seed(seed: int = RANDOM_STATE) -> None:
    random.seed(seed)
    np.random.seed(seed)
    try:
        import torch

        torch.manual_seed(seed)
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(seed)
    except ImportError:
        pass


def load_overrides(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    with Path(path).open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("override file must contain a JSON object")
    return data
