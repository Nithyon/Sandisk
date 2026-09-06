from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter1d
from sklearn.decomposition import PCA
from sklearn.preprocessing import StandardScaler


BLOCK_STAT_COLUMNS = [
    "block_mean", "block_std", "block_min", "block_max", "block_median", "block_p1", "block_p5",
    "block_p95", "block_p99", "block_skew", "block_kurtosis", "frac_abs_z_gt_2", "frac_abs_z_gt_3",
    "mean_lowest_20", "mean_highest_20", "max_rolling_mean", "max_rolling_std", "longest_abnormal_run",
    "largest_local_zscore",
]


def _longest_run(mask: np.ndarray) -> int:
    padded = np.concatenate(([False], mask, [False])).astype(np.int8)
    edges = np.diff(padded)
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1)
    return int((ends - starts).max()) if len(starts) else 0


def block_statistics(blocks: np.ndarray, rolling_window: int = 25, tail_count: int = 20) -> pd.DataFrame:
    values = np.asarray(blocks, dtype=np.float32)
    if values.ndim != 2 or values.shape[1] == 0:
        raise ValueError("blocks must be a non-empty 2D matrix")
    width = values.shape[1]
    tail = min(max(1, tail_count), width)
    window = min(max(1, rolling_window), width)
    mean = values.mean(axis=1)
    std = values.std(axis=1)
    safe_std = np.where(std > 0, std, 1.0)
    z = (values - mean[:, None]) / safe_std[:, None]
    block_skew = np.where(std > 0, np.mean(z ** 3, axis=1), 0.0)
    block_kurtosis = np.where(std > 0, np.mean(z ** 4, axis=1) - 3.0, 0.0)
    sorted_values = np.sort(values, axis=1)
    rolling_mean = uniform_filter1d(values, size=window, axis=1, mode="nearest")
    rolling_sq = uniform_filter1d(values * values, size=window, axis=1, mode="nearest")
    rolling_std = np.sqrt(np.maximum(rolling_sq - rolling_mean * rolling_mean, 0.0))
    data = np.column_stack(
        [
            mean, std, values.min(axis=1), values.max(axis=1), np.median(values, axis=1),
            np.percentile(values, 1, axis=1), np.percentile(values, 5, axis=1),
            np.percentile(values, 95, axis=1), np.percentile(values, 99, axis=1),
            block_skew, block_kurtosis,
            (np.abs(z) > 2).mean(axis=1), (np.abs(z) > 3).mean(axis=1),
            sorted_values[:, :tail].mean(axis=1), sorted_values[:, -tail:].mean(axis=1),
            rolling_mean.max(axis=1), rolling_std.max(axis=1),
            np.asarray([_longest_run(np.abs(row) > 2) for row in z]), np.abs(z).max(axis=1),
        ]
    ).astype(np.float32)
    return pd.DataFrame(data, columns=BLOCK_STAT_COLUMNS)


@dataclass
class BlockPCATransform:
    scaler: StandardScaler
    pca: PCA

    def transform(self, blocks: np.ndarray) -> np.ndarray:
        scaled = self.scaler.transform(np.asarray(blocks, dtype=np.float32))
        return self.pca.transform(scaled).astype(np.float32)


def fit_block_pca(blocks: np.ndarray, n_components: int, random_state: int = 42) -> BlockPCATransform:
    values = np.asarray(blocks, dtype=np.float32)
    if n_components > min(values.shape):
        raise ValueError("n_components exceeds the training matrix rank bound")
    scaler = StandardScaler(copy=True).fit(values)
    scaled = scaler.transform(values).astype(np.float32)
    pca = PCA(n_components=n_components, svd_solver="randomized", random_state=random_state).fit(scaled)
    return BlockPCATransform(scaler=scaler, pca=pca)
