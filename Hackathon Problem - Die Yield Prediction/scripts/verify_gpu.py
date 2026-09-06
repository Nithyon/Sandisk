from __future__ import annotations

import json
import platform

import numpy as np


def verify_pytorch() -> dict[str, object]:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError("torch.cuda.is_available() returned false")
    device = torch.device("cuda")
    left = torch.randn(64, 64, device=device)
    right = torch.randn(64, 64, device=device)
    result = left @ right
    torch.cuda.synchronize()
    return {
        "version": torch.__version__,
        "cuda_runtime": torch.version.cuda,
        "device": torch.cuda.get_device_name(0),
        "mean": float(result.mean().cpu()),
    }


def verify_xgboost() -> dict[str, object]:
    import xgboost as xgb

    rng = np.random.default_rng(42)
    x = rng.normal(size=(256, 8)).astype(np.float32)
    y = (x[:, 0] + x[:, 1] > 0).astype(np.int8)
    model = xgb.XGBClassifier(n_estimators=3, max_depth=2, tree_method="hist", device="cuda", random_state=42)
    model.fit(x, y, verbose=False)
    return {
        "version": xgb.__version__,
        "device": model.get_params()["device"],
        "boosted_rounds": model.get_booster().num_boosted_rounds(),
    }


def verify_catboost() -> dict[str, object]:
    import catboost

    rng = np.random.default_rng(42)
    x = rng.normal(size=(256, 8)).astype(np.float32)
    y = (x[:, 0] + x[:, 1] > 0).astype(np.int8)
    model = catboost.CatBoostClassifier(
        iterations=3, depth=2, task_type="GPU", devices="0", random_seed=42,
        verbose=False, allow_writing_files=False,
    )
    model.fit(x, y)
    probability = model.predict_proba(x)[:, 1]
    return {"version": catboost.__version__, "task_type": "GPU", "mean_probability": float(probability.mean())}


def main() -> None:
    checks = {"python": platform.python_version()}
    failures: dict[str, str] = {}
    for name, check in (("pytorch", verify_pytorch), ("xgboost", verify_xgboost), ("catboost", verify_catboost)):
        try:
            checks[name] = check()
        except Exception as exc:
            failures[name] = f"{type(exc).__name__}: {exc}"
    checks["failures"] = failures
    print(json.dumps(checks, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
