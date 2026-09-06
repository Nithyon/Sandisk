from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import torch


def save_tree_shap_summary(model, features, feature_names: list[str], output_dir: Path, max_samples: int = 2000) -> Path:
    import shap

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sample = features[: min(len(features), max_samples)]
    explanation = shap.TreeExplainer(model)(sample)
    values = np.asarray(explanation.values)
    if values.ndim == 3:
        values = values[:, :, -1]
    importance = pd.DataFrame(
        {"feature": feature_names, "mean_abs_shap": np.abs(values).mean(axis=0)}
    ).sort_values("mean_abs_shap", ascending=False)
    csv_path = output_dir / "shap_importance.csv"
    importance.to_csv(csv_path, index=False)
    top = importance.head(30).sort_values("mean_abs_shap")
    figure, axis = plt.subplots(figsize=(9, 8))
    axis.barh(top["feature"], top["mean_abs_shap"])
    axis.set_xlabel("Mean absolute SHAP value")
    figure.tight_layout()
    figure.savefig(output_dir / "shap_importance.png", dpi=160)
    plt.close(figure)
    return csv_path


def block_gradient_saliency(model: torch.nn.Module, macro: torch.Tensor, blocks: torch.Tensor) -> np.ndarray:
    model.eval()
    tracked_blocks = blocks.detach().clone().requires_grad_(True)
    logits = model(macro, tracked_blocks)
    gradient = torch.autograd.grad(logits.sum(), tracked_blocks)[0]
    return gradient.abs().detach().cpu().numpy().astype(np.float32)
