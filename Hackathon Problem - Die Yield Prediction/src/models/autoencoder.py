from __future__ import annotations

import torch
from torch import nn


def _layer(input_dim: int, output_dim: int) -> list[nn.Module]:
    return [nn.Linear(input_dim, output_dim), nn.BatchNorm1d(output_dim), nn.ReLU()]


class BlockAutoencoder(nn.Module):
    def __init__(self, input_dim: int = 2000, latent_dim: int = 32) -> None:
        super().__init__()
        self.encoder = nn.Sequential(*_layer(input_dim, 512), *_layer(512, 128), nn.Linear(128, latent_dim))
        self.decoder = nn.Sequential(*_layer(latent_dim, 128), *_layer(128, 512), nn.Linear(512, input_dim))

    def encode(self, blocks: torch.Tensor) -> torch.Tensor:
        return self.encoder(blocks)

    def forward(self, blocks: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        latent = self.encode(blocks)
        return self.decoder(latent), latent


def reconstruction_error(model: BlockAutoencoder, blocks: torch.Tensor) -> torch.Tensor:
    reconstruction, _ = model(blocks)
    return torch.mean((reconstruction - blocks) ** 2, dim=1)
