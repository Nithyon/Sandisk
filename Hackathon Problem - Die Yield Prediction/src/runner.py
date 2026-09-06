from __future__ import annotations

import json
import hashlib
import time
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.model_selection import GroupShuffleSplit
from sklearn.preprocessing import StandardScaler

from .artifacts import atomic_json
from .block_features import BLOCK_STAT_COLUMNS, block_statistics, fit_block_pca
from .config import MODEL_DEFAULTS, ProjectPaths, RANDOM_STATE, set_global_seed
from .data_loader import feature_columns, load_cache
from .evaluation import append_benchmark_result, classification_metrics, group_folds, select_f1_threshold
from .models.autoencoder import BlockAutoencoder
from .models.catboost_model import build_catboost, fit_catboost
from .models.cnn1d import CNN1DFusion
from .models.common_torch import predict_torch, train_autoencoder, train_binary_model
from .models.lightgbm_model import build_lightgbm, fit_lightgbm
from .models.mlp import MLPClassifier
from .models.xgboost_model import build_xgboost, fit_xgboost


EXPERIMENT_REGISTRY: dict[str, str] = {
    "A1": "catboost", "A2": "xgboost", "A3": "lightgbm", "A4": "mlp",
    "B1": "catboost_stats", "B2": "xgboost_pca", "AE32": "autoencoder",
    "AE64": "autoencoder", "B3a": "catboost_ae", "B3b": "catboost_ae_error",
    "B4": "xgboost_ae", "B5": "cnn", "B6": "mlp_ae",
}

MODEL_NAMES = {
    "A1": "CatBoost", "A2": "XGBoost", "A3": "LightGBM", "A4": "MLP",
    "B1": "CatBoost + block statistics", "B2": "XGBoost + PCA(blocks)",
    "AE32": "Block autoencoder 32", "AE64": "Block autoencoder 64",
    "B3a": "AE embedding + CatBoost", "B3b": "AE embedding + reconstruction error + CatBoost",
    "B4": "AE embedding + XGBoost", "B5": "1D CNN fusion", "B6": "AE embedding + MLP",
}


def experiment_family(experiment_id: str) -> str:
    try:
        family = EXPERIMENT_REGISTRY[experiment_id]
    except KeyError as exc:
        raise ValueError(f"unknown experiment {experiment_id!r}") from exc
    if family.startswith("catboost"):
        return "catboost"
    if family.startswith("xgboost"):
        return "xgboost"
    return family


def model_a_matrix(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    if "label" not in frame:
        raise ValueError("labeled training data is required")
    eligible = frame["old_label"].eq(0)
    columns = [column for column in frame.columns if column not in {"wafer_id", "old_label", "label"}]
    x = frame.loc[eligible, columns].to_numpy(dtype=np.float32)
    y = frame.loc[eligible, "label"].to_numpy(dtype=np.int8)
    groups = frame.loc[eligible, "wafer_id"].to_numpy()
    row_index = frame.index[eligible].to_numpy(dtype=np.int64)
    return x, y, groups, row_index, columns


def positive_weight(y: np.ndarray) -> float:
    positives = int(np.sum(y == 1))
    negatives = int(np.sum(y == 0))
    if positives == 0:
        raise ValueError("training fold has no positive examples")
    return negatives / positives


def supervised_inner_split(groups: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    inner_train, inner_stop = next(
        GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=RANDOM_STATE).split(
            np.zeros(len(groups), dtype=np.int8), groups=groups
        )
    )
    if set(groups[inner_train]).intersection(groups[inner_stop]):
        raise RuntimeError("wafer leakage detected in supervised early-stopping split")
    return inner_train, inner_stop


def run_tag(
    experiment_id: str,
    overrides: dict[str, Any],
    *,
    n_splits: int | None = None,
    device: str | None = None,
    source_fingerprint: str | None = None,
) -> str:
    if not overrides and n_splits is None and device is None and source_fingerprint is None:
        return f"{experiment_id}_default"
    signature = {
        "overrides": overrides,
        "n_splits": n_splits,
        "device": device,
        "source_fingerprint": source_fingerprint,
        "seed": RANDOM_STATE,
        "protocol": "grouped-outer-inner-stop-v2",
    }
    encoded = json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{experiment_id}_{hashlib.sha256(encoded).hexdigest()[:10]}"


def _block_stats_batched(blocks: np.ndarray, row_index: np.ndarray, batch_size: int = 2048) -> np.ndarray:
    parts = [
        block_statistics(np.asarray(blocks[row_index[start : start + batch_size]]))
        for start in range(0, len(row_index), batch_size)
    ]
    return pd.concat(parts, ignore_index=True).to_numpy(dtype=np.float32)


def _torch_device(device: str) -> torch.device:
    if device == "gpu":
        if not torch.cuda.is_available():
            raise RuntimeError("GPU requested but torch.cuda.is_available() is false")
        return torch.device("cuda")
    return torch.device("cpu")


def _encode_autoencoder(model: BlockAutoencoder, values: np.ndarray, device: torch.device, batch_size: int = 1024):
    latent_parts: list[np.ndarray] = []
    error_parts: list[np.ndarray] = []
    model.eval()
    with torch.no_grad():
        for start in range(0, len(values), batch_size):
            batch = torch.from_numpy(np.asarray(values[start : start + batch_size], dtype=np.float32)).to(device)
            reconstruction, latent = model(batch)
            latent_parts.append(latent.cpu().numpy().astype(np.float32))
            error_parts.append(torch.mean((reconstruction - batch) ** 2, dim=1).cpu().numpy().astype(np.float32))
    return np.concatenate(latent_parts), np.concatenate(error_parts)


def _ae_representation(
    train_blocks: np.ndarray,
    valid_blocks: np.ndarray,
    train_groups: np.ndarray,
    *,
    latent_dim: int,
    device: torch.device,
    max_epochs: int,
    batch_size: int,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, BlockAutoencoder, StandardScaler]:
    inner_train, inner_valid = next(
        GroupShuffleSplit(n_splits=1, test_size=0.15, random_state=RANDOM_STATE).split(train_blocks, groups=train_groups)
    )
    scaler = StandardScaler().fit(np.asarray(train_blocks[inner_train], dtype=np.float32))
    scaled_train = scaler.transform(np.asarray(train_blocks, dtype=np.float32)).astype(np.float32)
    scaled_valid = scaler.transform(np.asarray(valid_blocks, dtype=np.float32)).astype(np.float32)
    model = BlockAutoencoder(input_dim=train_blocks.shape[1], latent_dim=latent_dim)
    model = train_autoencoder(
        model,
        torch.from_numpy(scaled_train[inner_train]),
        torch.from_numpy(scaled_train[inner_valid]),
        device=device,
        batch_size=batch_size,
        max_epochs=max_epochs,
    )
    train_latent, train_error = _encode_autoencoder(model, scaled_train, device)
    valid_latent, valid_error = _encode_autoencoder(model, scaled_valid, device)
    return train_latent, valid_latent, train_error, valid_error, model, scaler


def _fit_tree(
    experiment_id: str,
    x_fit: np.ndarray,
    y_fit: np.ndarray,
    x_stop: np.ndarray,
    y_stop: np.ndarray,
    x_predict: np.ndarray,
    columns: list[str],
    *,
    device: str,
    overrides: dict[str, Any],
):
    family = experiment_family(experiment_id)
    if family == "catboost":
        fit_frame = pd.DataFrame(x_fit, columns=columns)
        stop_frame = pd.DataFrame(x_stop, columns=columns)
        predict_frame = pd.DataFrame(x_predict, columns=columns)
        cat_features: list[int] = []
        if "zone_id" in columns:
            zone_index = columns.index("zone_id")
            fit_frame["zone_id"] = fit_frame["zone_id"].round().astype("int16").astype(str)
            stop_frame["zone_id"] = stop_frame["zone_id"].round().astype("int16").astype(str)
            predict_frame["zone_id"] = predict_frame["zone_id"].round().astype("int16").astype(str)
            cat_features = [zone_index]
        model = build_catboost(overrides, device=device)
        fit_catboost(model, fit_frame, y_fit, stop_frame, y_stop, cat_features=cat_features)
        probability = model.predict_proba(predict_frame)[:, 1]
    elif family == "xgboost":
        model = build_xgboost(overrides, device=device, scale_pos_weight=positive_weight(y_fit))
        fit_xgboost(model, x_fit, y_fit, x_stop, y_stop)
        probability = model.predict_proba(x_predict)[:, 1]
    elif family == "lightgbm":
        model = build_lightgbm(overrides, device=device)
        fit_lightgbm(model, x_fit, y_fit, x_stop, y_stop)
        probability = model.predict_proba(x_predict)[:, 1]
    else:
        raise ValueError(f"{experiment_id} is not a tree experiment")
    return model, np.asarray(probability, dtype=np.float32)


def _neural_fold(
    experiment_id: str,
    x_train: np.ndarray,
    y_train: np.ndarray,
    x_predict: np.ndarray,
    train_blocks: np.ndarray | None,
    predict_blocks: np.ndarray | None,
    train_groups: np.ndarray,
    *,
    device_name: str,
    overrides: dict[str, Any],
):
    device = _torch_device(device_name)
    inner_train, inner_stop = supervised_inner_split(train_groups)
    macro_scaler = StandardScaler().fit(x_train[inner_train])
    macro_train = macro_scaler.transform(x_train).astype(np.float32)
    macro_predict = macro_scaler.transform(x_predict).astype(np.float32)
    batch_size = int(overrides.get("batch_size", 1024 if experiment_id == "A4" else 256))
    max_epochs = int(overrides.get("max_epochs", 50 if experiment_id in {"A4", "B6"} else 30))
    if experiment_id == "A4":
        model = MLPClassifier(macro_train.shape[1])
        train_tensors = (torch.from_numpy(macro_train),)
        predict_tensors = (torch.from_numpy(macro_predict),)
        aux = {"macro_scaler": macro_scaler}
    elif experiment_id == "B5":
        if train_blocks is None or predict_blocks is None:
            raise ValueError("B5 requires block readings")
        block_scaler = StandardScaler().fit(np.asarray(train_blocks[inner_train], dtype=np.float32))
        block_train = block_scaler.transform(np.asarray(train_blocks, dtype=np.float32)).astype(np.float32)
        block_predict = block_scaler.transform(np.asarray(predict_blocks, dtype=np.float32)).astype(np.float32)
        model = CNN1DFusion(macro_train.shape[1])
        train_tensors = (torch.from_numpy(macro_train), torch.from_numpy(block_train))
        predict_tensors = (torch.from_numpy(macro_predict), torch.from_numpy(block_predict))
        aux = {"macro_scaler": macro_scaler, "block_scaler": block_scaler}
    elif experiment_id == "B6":
        if train_blocks is None or predict_blocks is None:
            raise ValueError("B6 requires block readings")
        latent_dim = int(overrides.get("latent_dim", 32))
        train_latent, valid_latent, _, _, ae, block_scaler = _ae_representation(
            train_blocks, predict_blocks, train_groups, latent_dim=latent_dim, device=device,
            max_epochs=int(overrides.get("ae_epochs", 50)), batch_size=int(overrides.get("ae_batch_size", 512)),
        )
        combined_train = np.column_stack((macro_train, train_latent)).astype(np.float32)
        combined_predict = np.column_stack((macro_predict, valid_latent)).astype(np.float32)
        model = MLPClassifier(combined_train.shape[1])
        train_tensors = (torch.from_numpy(combined_train),)
        predict_tensors = (torch.from_numpy(combined_predict),)
        aux = {"macro_scaler": macro_scaler, "block_scaler": block_scaler, "autoencoder": ae}
    else:
        raise ValueError(f"unsupported neural experiment {experiment_id}")
    result = train_binary_model(
        model,
        tuple(tensor[inner_train] for tensor in train_tensors),
        torch.from_numpy(y_train[inner_train]),
        tuple(tensor[inner_stop] for tensor in train_tensors),
        torch.from_numpy(y_train[inner_stop]),
        device=device, pos_weight=positive_weight(y_train[inner_train]), batch_size=batch_size, max_epochs=max_epochs,
        patience=int(overrides.get("patience", 5)), learning_rate=float(overrides.get("learning_rate", 1e-3)),
    )
    return result.model, predict_torch(result.model, predict_tensors, device=device, batch_size=batch_size), aux


def run_cross_validation(
    experiment_id: str,
    paths: ProjectPaths,
    *,
    device: str = "gpu",
    overrides: dict[str, Any] | None = None,
    n_splits: int = 5,
    save_fold_models: bool = False,
) -> dict[str, Any]:
    if experiment_id not in EXPERIMENT_REGISTRY:
        raise ValueError(f"unknown experiment {experiment_id}")
    paths.ensure_output_dirs()
    set_global_seed()
    overrides = dict(overrides or {})
    metadata_path = paths.cache_dir / "train" / "metadata.json"
    source_id = None
    if metadata_path.exists():
        with metadata_path.open("r", encoding="utf-8") as handle:
            source_id = json.load(handle).get("fingerprint")
    tag = run_tag(
        experiment_id,
        overrides,
        n_splits=n_splits,
        device=device,
        source_fingerprint=source_id,
    )
    probability_path = paths.predictions_dir / f"{tag}_oof.csv"
    log_path = paths.logs_dir / f"{tag}_cv.json"
    if probability_path.exists() or log_path.exists():
        raise FileExistsError(f"run artifacts already exist for {tag}; change configuration or remove them explicitly")
    macro, block_matrix = load_cache(paths.cache_dir / "train")
    base_x, y, groups, row_index, base_columns = model_a_matrix(macro)
    if experiment_id in {"AE32", "AE64"}:
        return run_autoencoder_oof(
            experiment_id, block_matrix[row_index], groups, row_index, paths,
            tag=tag,
            device=device, overrides=overrides, n_splits=n_splits,
        )

    oof = np.full(len(y), np.nan, dtype=np.float32)
    total_started = time.perf_counter()
    preprocessing_time = 0.0
    training_time = 0.0
    prep_started = time.perf_counter()
    stats = _block_stats_batched(block_matrix, row_index) if experiment_id == "B1" else None
    preprocessing_time += time.perf_counter() - prep_started
    torch_device = _torch_device(device) if experiment_id in {"B3a", "B3b", "B4"} else None
    for fold, (train_idx, valid_idx) in enumerate(group_folds(y, groups, n_splits=n_splits), start=1):
        fold_prep_started = time.perf_counter()
        x_train, x_valid = base_x[train_idx], base_x[valid_idx]
        columns = list(base_columns)
        needs_fold_blocks = experiment_id in {"B2", "B3a", "B3b", "B4", "B5", "B6"}
        train_blocks = block_matrix[row_index[train_idx]] if needs_fold_blocks else None
        valid_blocks = block_matrix[row_index[valid_idx]] if needs_fold_blocks else None
        model: Any
        auxiliary: dict[str, Any] = {}
        if experiment_id == "B1":
            x_train = np.column_stack((x_train, stats[train_idx])).astype(np.float32)
            x_valid = np.column_stack((x_valid, stats[valid_idx])).astype(np.float32)
            columns.extend(BLOCK_STAT_COLUMNS)
        elif experiment_id == "B2":
            assert train_blocks is not None and valid_blocks is not None
            components = int(overrides.get("pca_components", 50))
            pca = fit_block_pca(train_blocks, components)
            x_train = np.column_stack((x_train, pca.transform(train_blocks))).astype(np.float32)
            x_valid = np.column_stack((x_valid, pca.transform(valid_blocks))).astype(np.float32)
            columns.extend([f"block_pc_{i + 1}" for i in range(components)])
            auxiliary["pca"] = pca
        elif experiment_id in {"B3a", "B3b", "B4"}:
            assert train_blocks is not None and valid_blocks is not None
            latent_dim = int(overrides.get("latent_dim", 32))
            train_latent, valid_latent, train_error, valid_error, ae, scaler = _ae_representation(
                train_blocks, valid_blocks, groups[train_idx], latent_dim=latent_dim, device=torch_device,
                max_epochs=int(overrides.get("ae_epochs", 50)), batch_size=int(overrides.get("ae_batch_size", 512)),
            )
            x_train = np.column_stack((x_train, train_latent)).astype(np.float32)
            x_valid = np.column_stack((x_valid, valid_latent)).astype(np.float32)
            columns.extend([f"ae_{i + 1}" for i in range(latent_dim)])
            if experiment_id == "B3b":
                x_train = np.column_stack((x_train, train_error)).astype(np.float32)
                x_valid = np.column_stack((x_valid, valid_error)).astype(np.float32)
                columns.append("reconstruction_mse")
            auxiliary.update({"autoencoder": ae, "block_scaler": scaler})

        preprocessing_time += time.perf_counter() - fold_prep_started

        model_started = time.perf_counter()
        if experiment_id in {"A4", "B5", "B6"}:
            model, probability, auxiliary = _neural_fold(
                experiment_id, x_train, y[train_idx], x_valid,
                train_blocks if experiment_id != "A4" else None,
                valid_blocks if experiment_id != "A4" else None,
                groups[train_idx], device_name=device, overrides=overrides,
            )
        else:
            tree_id = {"B1": "A1", "B2": "A2", "B3a": "A1", "B3b": "A1", "B4": "A2"}.get(experiment_id, experiment_id)
            model_overrides = dict(overrides)
            for key in ("pca_components", "latent_dim", "ae_epochs", "ae_batch_size"):
                model_overrides.pop(key, None)
            inner_train, inner_stop = supervised_inner_split(groups[train_idx])
            model, probability = _fit_tree(
                tree_id,
                x_train[inner_train],
                y[train_idx][inner_train],
                x_train[inner_stop],
                y[train_idx][inner_stop],
                x_valid,
                columns,
                device=device, overrides=model_overrides,
            )
        training_time += time.perf_counter() - model_started
        oof[valid_idx] = probability
        if save_fold_models:
            fold_dir = paths.models_dir / tag
            fold_dir.mkdir(parents=True, exist_ok=True)
            joblib.dump({"model": model, "auxiliary": auxiliary, "columns": columns}, fold_dir / f"fold_{fold}.joblib")

    if np.isnan(oof).any():
        raise RuntimeError("not every eligible row received an out-of-fold probability")
    threshold = select_f1_threshold(y, oof)
    metrics = classification_metrics(y, oof, threshold)
    pd.DataFrame(
        {
            "source_row": row_index,
            "wafer_id": groups,
            "die_row": macro.loc[row_index, "die_row"].to_numpy(dtype=np.int16),
            "die_col": macro.loc[row_index, "die_col"].to_numpy(dtype=np.int16),
            "label": y,
            "probability": oof,
            "threshold": threshold,
            "predicted_label": (oof >= threshold).astype(np.int8),
        }
    ).to_csv(probability_path, index=False)
    result = {
        "experiment_id": experiment_id,
        "model_name": MODEL_NAMES[experiment_id],
        "model_variant": f"{n_splits}-fold grouped CV ({tag})",
        **metrics,
        "preprocessing_time": preprocessing_time,
        "training_time": training_time,
        "total_time": time.perf_counter() - total_started,
        "notes": f"device={device}; overrides={json.dumps(overrides, sort_keys=True)}",
    }
    append_benchmark_result(paths.results_dir / "benchmark_results.csv", result)
    atomic_json(log_path, result)
    return result


def run_autoencoder_oof(
    experiment_id: str,
    blocks: np.ndarray,
    groups: np.ndarray,
    row_index: np.ndarray,
    paths: ProjectPaths,
    *,
    tag: str,
    device: str,
    overrides: dict[str, Any],
    n_splits: int,
) -> dict[str, Any]:
    latent_dim = 32 if experiment_id == "AE32" else 64
    artifact_dir = paths.models_dir / tag
    cache_dir = paths.cache_dir / tag.lower()
    if artifact_dir.exists() or cache_dir.exists():
        raise FileExistsError(f"autoencoder artifacts already exist for {tag}; remove them explicitly to rerun")
    embedding = np.empty((len(blocks), latent_dim), dtype=np.float32)
    error = np.empty(len(blocks), dtype=np.float32)
    torch_device = _torch_device(device)
    dummy_y = np.zeros(len(blocks), dtype=np.int8)
    started = time.perf_counter()
    for fold, (train_idx, valid_idx) in enumerate(group_folds(dummy_y, groups, n_splits=n_splits), start=1):
        _, valid_latent, _, valid_error, model, scaler = _ae_representation(
            blocks[train_idx], blocks[valid_idx], groups[train_idx], latent_dim=latent_dim, device=torch_device,
            max_epochs=int(overrides.get("max_epochs", 50)), batch_size=int(overrides.get("batch_size", 512)),
        )
        embedding[valid_idx] = valid_latent
        error[valid_idx] = valid_error
        artifact_dir.mkdir(parents=True, exist_ok=True)
        torch.save(model.state_dict(), artifact_dir / f"fold_{fold}.pt")
        joblib.dump(scaler, artifact_dir / f"fold_{fold}_scaler.joblib")
    cache_dir.mkdir(parents=True, exist_ok=True)
    np.save(cache_dir / "oof_embedding.float32.npy", embedding)
    np.save(cache_dir / "oof_reconstruction_mse.float32.npy", error)
    manifest = {
        "experiment_id": experiment_id,
        "latent_dim": latent_dim,
        "rows": len(blocks),
        "source_rows_file": "source_rows.npy",
        "seconds": time.perf_counter() - started,
        "note": "Preprocessing artifact only; no classifier metrics are recorded.",
    }
    np.save(cache_dir / "source_rows.npy", row_index)
    atomic_json(cache_dir / "metadata.json", manifest)
    return manifest


def format_summary(result: dict[str, Any]) -> str:
    if "fail_f1" not in result:
        return f"{result['experiment_id']} preprocessing complete in {result['seconds']:.1f}s"
    return "\n".join(
        [
            f"{result['experiment_id']} {result['model_name']}",
            f"Fail F1: {result['fail_f1']:.6f}",
            f"Precision: {result['precision']:.6f}",
            f"Recall: {result['recall']:.6f}",
            f"PR-AUC: {result['pr_auc']:.6f}",
            f"ROC-AUC: {result['roc_auc']:.6f}",
            f"Accuracy: {result['accuracy']:.6f}",
            f"Threshold: {result['threshold']:.6f}",
            f"Training time: {result['training_time']:.1f}s",
        ]
    )
