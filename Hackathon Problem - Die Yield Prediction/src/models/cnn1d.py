from __future__ import annotations

import torch
from torch import nn


class CNN1DFusion(nn.Module):
    def __init__(self, macro_dim: int, dropout: float = 0.3) -> None:
        super().__init__()
        self.block_branch = nn.Sequential(
            nn.Conv1d(1, 32, kernel_size=7, padding=3), nn.BatchNorm1d(32), nn.ReLU(), nn.MaxPool1d(4),
            nn.Conv1d(32, 64, kernel_size=5, padding=2), nn.BatchNorm1d(64), nn.ReLU(), nn.MaxPool1d(4),
            nn.Conv1d(64, 128, kernel_size=3, padding=1), nn.BatchNorm1d(128), nn.ReLU(), nn.AdaptiveAvgPool1d(1),
        )
        self.head = nn.Sequential(nn.Linear(macro_dim + 128, 128), nn.ReLU(), nn.Dropout(dropout), nn.Linear(128, 1))

    def block_embedding(self, blocks: torch.Tensor) -> torch.Tensor:
        return self.block_branch(blocks.unsqueeze(1)).squeeze(2)

    def forward(self, macro: torch.Tensor, blocks: torch.Tensor) -> torch.Tensor:
        fused = torch.cat((macro, self.block_embedding(blocks)), dim=1)
        return self.head(fused).squeeze(1)
