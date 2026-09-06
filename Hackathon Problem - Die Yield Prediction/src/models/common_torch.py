from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Callable

import numpy as np
import torch
from sklearn.metrics import average_precision_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


@dataclass
class TorchFitResult:
    model: nn.Module
    best_epoch: int
    best_pr_auc: float


def train_binary_model(
    model: nn.Module,
    train_tensors: tuple[torch.Tensor, ...],
    train_y: torch.Tensor,
    valid_tensors: tuple[torch.Tensor, ...],
    valid_y: torch.Tensor,
    *,
    device: torch.device,
    pos_weight: float,
    batch_size: int,
    max_epochs: int,
    patience: int = 5,
    learning_rate: float = 1e-3,
) -> TorchFitResult:
    model = model.to(device)
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=torch.tensor([pos_weight], device=device))
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    dataset = TensorDataset(*train_tensors, train_y)
    if len(dataset) < 2:
        raise ValueError("BatchNorm training requires at least two training rows")
    drop_last = len(dataset) % batch_size == 1
    loader = DataLoader(
        dataset, batch_size=batch_size, shuffle=True, drop_last=drop_last, pin_memory=device.type == "cuda"
    )
    best_score, best_epoch, stale = -np.inf, -1, 0
    best_state = copy.deepcopy(model.state_dict())
    for epoch in range(max_epochs):
        model.train()
        for batch in loader:
            *features, target = [value.to(device, non_blocking=True) for value in batch]
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(*features), target.float())
            loss.backward()
            optimizer.step()
        probability = predict_torch(model, valid_tensors, device=device, batch_size=batch_size)
        score = average_precision_score(valid_y.cpu().numpy(), probability)
        if score > best_score + 1e-8:
            best_score, best_epoch, stale = float(score), epoch, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            stale += 1
            if stale >= patience:
                break
    model.load_state_dict(best_state)
    return TorchFitResult(model=model, best_epoch=best_epoch, best_pr_auc=best_score)


def predict_torch(
    model: nn.Module, tensors: tuple[torch.Tensor, ...], *, device: torch.device, batch_size: int
) -> np.ndarray:
    model.eval()
    loader = DataLoader(TensorDataset(*tensors), batch_size=batch_size, shuffle=False)
    output: list[np.ndarray] = []
    with torch.no_grad():
        for batch in loader:
            logits = model(*[value.to(device, non_blocking=True) for value in batch])
            output.append(torch.sigmoid(logits).cpu().numpy())
    return np.concatenate(output)


def train_autoencoder(
    model: nn.Module,
    train_blocks: torch.Tensor,
    valid_blocks: torch.Tensor,
    *,
    device: torch.device,
    batch_size: int = 512,
    max_epochs: int = 50,
    patience: int = 5,
    learning_rate: float = 1e-3,
) -> nn.Module:
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=learning_rate)
    dataset = TensorDataset(train_blocks)
    if len(dataset) < 2:
        raise ValueError("BatchNorm autoencoder training requires at least two training rows")
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True, drop_last=len(dataset) % batch_size == 1)
    best_loss, stale = np.inf, 0
    best_state = copy.deepcopy(model.state_dict())
    for _ in range(max_epochs):
        model.train()
        for (batch,) in loader:
            batch = batch.to(device)
            optimizer.zero_grad(set_to_none=True)
            reconstruction, _ = model(batch)
            loss = nn.functional.mse_loss(reconstruction, batch)
            loss.backward()
            optimizer.step()
        model.eval()
        losses = []
        with torch.no_grad():
            for (batch,) in DataLoader(TensorDataset(valid_blocks), batch_size=batch_size):
                batch = batch.to(device)
                reconstruction, _ = model(batch)
                losses.append(nn.functional.mse_loss(reconstruction, batch).item())
        valid_loss = float(np.mean(losses))
        if valid_loss < best_loss - 1e-8:
            best_loss, stale = valid_loss, 0
            best_state = copy.deepcopy(model.state_dict())
        else:
            stale += 1
            if stale >= patience:
                break
    model.load_state_dict(best_state)
    return model
