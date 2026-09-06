from __future__ import annotations

import torch
from torch import nn


class MLPClassifier(nn.Module):
    def __init__(self, input_dim: int, dropout: float = 0.3) -> None:
        super().__init__()
        layers: list[nn.Module] = []
        previous = input_dim
        for width in (512, 256, 128):
            layers.extend([nn.Linear(previous, width), nn.BatchNorm1d(width), nn.ReLU(), nn.Dropout(dropout)])
            previous = width
        layers.append(nn.Linear(previous, 1))
        self.network = nn.Sequential(*layers)

    def forward(self, macro: torch.Tensor) -> torch.Tensor:
        return self.network(macro).squeeze(1)
