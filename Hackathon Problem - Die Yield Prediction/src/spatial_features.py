from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.ndimage import convolve
from scipy.spatial import cKDTree


SPATIAL_COLUMNS = [
    "die_row",
    "die_col",
    "radial_distance",
    "zone_id",
    "neighborhood_old_fail_rate",
    "wafer_old_fail_rate",
    "nearest_old_fail_distance",
    "has_old_failure",
]


def _zone_axis(values: np.ndarray, bins: int) -> np.ndarray:
    low = float(values.min())
    high = float(values.max())
    if high == low:
        return np.zeros(values.shape, dtype=np.int16)
    scaled = (values - low) / (high - low)
    return np.minimum((scaled * bins).astype(np.int16), bins - 1)


def _one_wafer(frame: pd.DataFrame, zone_rows: int, zone_cols: int, window: int) -> pd.DataFrame:
    rows = frame["die_row"].to_numpy(dtype=np.int32)
    cols = frame["die_col"].to_numpy(dtype=np.int32)
    old = frame["old_label"].to_numpy(dtype=np.int8)
    row0, col0 = int(rows.min()), int(cols.min())
    rr, cc = rows - row0, cols - col0

    shape = (int(rr.max()) + 1, int(cc.max()) + 1)
    valid_grid = np.zeros(shape, dtype=np.float32)
    fail_grid = np.zeros(shape, dtype=np.float32)
    valid_grid[rr, cc] = 1.0
    fail_grid[rr, cc] = old
    kernel = np.ones((window, window), dtype=np.float32)
    valid_count = convolve(valid_grid, kernel, mode="constant", cval=0.0)
    fail_count = convolve(fail_grid, kernel, mode="constant", cval=0.0)
    local_rate = np.divide(
        fail_count[rr, cc], valid_count[rr, cc], out=np.zeros(len(frame), dtype=np.float32), where=valid_count[rr, cc] > 0
    )

    center_row = (float(rows.min()) + float(rows.max())) / 2.0
    center_col = (float(cols.min()) + float(cols.max())) / 2.0
    radial = np.hypot(rows - center_row, cols - center_col)
    radial_max = float(radial.max())
    if radial_max > 0:
        radial = radial / radial_max

    zr = _zone_axis(rows, zone_rows)
    zc = _zone_axis(cols, zone_cols)
    zone = zr * zone_cols + zc

    failed_coordinates = np.column_stack((rows[old == 1], cols[old == 1]))
    if len(failed_coordinates):
        nearest = cKDTree(failed_coordinates).query(np.column_stack((rows, cols)), k=1)[0]
        has_old_failure = np.ones(len(frame), dtype=np.int8)
    else:
        sentinel = float(np.hypot(rows.max() - rows.min(), cols.max() - cols.min()) + 1.0)
        nearest = np.full(len(frame), sentinel, dtype=np.float32)
        has_old_failure = np.zeros(len(frame), dtype=np.int8)

    return pd.DataFrame(
        {
            "die_row": rows.astype(np.float32),
            "die_col": cols.astype(np.float32),
            "radial_distance": radial.astype(np.float32),
            "zone_id": zone.astype(np.int16),
            "neighborhood_old_fail_rate": local_rate.astype(np.float32),
            "wafer_old_fail_rate": np.full(len(frame), old.mean(), dtype=np.float32),
            "nearest_old_fail_distance": np.asarray(nearest, dtype=np.float32),
            "has_old_failure": has_old_failure,
        },
        index=frame.index,
    )


def compute_spatial_features(
    frame: pd.DataFrame, zone_rows: int = 4, zone_cols: int = 4, window: int = 5
) -> pd.DataFrame:
    required = {"wafer_id", "die_row", "die_col", "old_label"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"missing spatial columns: {sorted(missing)}")
    if window < 1 or window % 2 == 0:
        raise ValueError("window must be a positive odd integer")
    if frame.empty:
        return pd.DataFrame(index=frame.index, columns=SPATIAL_COLUMNS)
    parts = [
        _one_wafer(group, zone_rows, zone_cols, window)
        for _, group in frame.groupby("wafer_id", sort=False, observed=True)
    ]
    return pd.concat(parts).loc[frame.index, SPATIAL_COLUMNS]
