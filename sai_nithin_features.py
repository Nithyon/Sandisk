"""
Shared data loading + spatial feature engineering for Sai Nithin's experiment lane
(PCA + Logistic Regression, PCA + XGBoost -- Model A and Model B).

Design notes (see COORDINATION_LOG.md, "PCA DEFINITION DECISION" checkpoint):
  - Model A representation = StandardScaler+PCA(500 parametric features) + spatial features
    (spatial features are NEVER put through PCA).
  - Model B representation = exact Model A representation + a SEPARATELY fitted
    StandardScaler+PCA on the 2000 block readings.
  - Spatial features are computed on the FULL wafer (old_label 0 and 1 together),
    then rows are filtered to old_label == 0 afterwards. Filtering first would zero
    every spatial feature.
  - All scalers/PCA objects are fit on the training fold only -- never on validation
    or test data -- to avoid leakage.

Caching: building spatial features and parsing the 2000-value block_readings string
column is the expensive part of preprocessing. Results are cached to `cache/` as
.npz/.parquet so it is paid once, matching the tracker's "cache it" instruction.
"""
import hashlib
import os
import time
import numpy as np
import pandas as pd
from scipy.ndimage import uniform_filter

CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

FEATURE_COLS = [f"feature_{i}" for i in range(1, 501)]


def _cache_path(name):
    return os.path.join(CACHE_DIR, name)


def _source_cache_key(csv_path, nrows):
    """Identify the exact source slice used to build cached derived arrays."""
    source = os.path.realpath(os.fspath(csv_path))
    stat = os.stat(source)
    identity = f"{source}|{stat.st_size}|{stat.st_mtime_ns}|{nrows}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def build_spatial_features(df):
    """
    Compute neighborhood fail-density (5x5 window, matching generate_data.py's own
    `neighborhood_window: 5` config) and normalized radial distance from wafer
    center, for every die in every wafer.

    MUST be called on the full wafer (old_label 0 and 1 together) -- filtering to
    old_label == 0 first makes every neighbor-density value zero, since there would
    be no fails left to see.

    Returns a DataFrame with columns [wafer_id, die_row, die_col,
    spatial_neighbor_fail_density, spatial_radial_dist] aligned to `df`'s row order.
    """
    out_density = np.zeros(len(df), dtype=np.float32)
    out_radial = np.zeros(len(df), dtype=np.float32)

    for wafer_id, group in df.groupby("wafer_id", sort=False):
        rows = group["die_row"].to_numpy()
        cols = group["die_col"].to_numpy()
        old_label = group["old_label"].to_numpy()

        n_rows = rows.max() + 1
        n_cols = cols.max() + 1

        valid_grid = np.zeros((n_rows, n_cols), dtype=np.float64)
        fail_grid = np.zeros((n_rows, n_cols), dtype=np.float64)
        valid_grid[rows, cols] = 1.0
        fail_grid[rows, cols] = old_label.astype(np.float64)

        fail_sum = uniform_filter(fail_grid, size=5, mode="constant", cval=0.0)
        valid_sum = uniform_filter(valid_grid, size=5, mode="constant", cval=0.0)
        with np.errstate(divide="ignore", invalid="ignore"):
            density_grid = np.where(valid_sum > 0, fail_sum / valid_sum, 0.0)

        cy, cx = n_rows / 2.0, n_cols / 2.0
        y_coords, x_coords = np.mgrid[0:n_rows, 0:n_cols]
        radial_grid = np.sqrt((y_coords - cy) ** 2 + (x_coords - cx) ** 2)
        max_r = radial_grid.max()
        if max_r > 0:
            radial_grid = radial_grid / max_r

        idx = group.index.to_numpy()
        pos = df.index.get_indexer(idx)
        out_density[pos] = density_grid[rows, cols]
        out_radial[pos] = radial_grid[rows, cols]

    return pd.DataFrame(
        {
            "spatial_neighbor_fail_density": out_density,
            "spatial_radial_dist": out_radial,
        },
        index=df.index,
    )


def parse_block_readings(series, k=2000, dtype=np.float32):
    """Vectorized-ish parse of the space-separated block_readings string column
    into a (n_rows, k) float matrix. This is the expensive preprocessing step for
    Model B -- cache the result."""
    n = len(series)
    out = np.empty((n, k), dtype=dtype)
    for i, s in enumerate(series.to_numpy()):
        out[i] = np.fromstring(s, sep=" ", dtype=dtype, count=k)
    return out


def load_split(csv_path, cache_tag, need_blocks, nrows=None, require_label=True):
    """
    Load one CSV (train.csv or test.csv), build spatial features on the FULL wafer,
    then return everything needed downstream. Caches parsed block matrix and
    spatial features to disk so re-running costs nothing after the first pass.

    Returns dict with:
      meta        -- DataFrame[wafer_id, die_row, die_col, old_label, label]
      X_param     -- (n, 500) float32 parametric feature matrix
      X_spatial   -- (n, 2) float32 spatial feature matrix
      X_block     -- (n, 2000) float32 block matrix, or None if need_blocks=False
      timings     -- dict of stage -> seconds
    """
    timings = {}
    t0 = time.time()

    metadata_columns = ["wafer_id", "die_row", "die_col", "old_label"]
    if require_label:
        metadata_columns.append("label")
    usecols = metadata_columns + FEATURE_COLS
    if need_blocks:
        usecols.append("block_readings")

    dtypes = {c: "float32" for c in FEATURE_COLS}
    dtypes.update({"die_row": "int32", "die_col": "int32", "old_label": "int8"})
    if require_label:
        dtypes["label"] = "int8"
    if need_blocks:
        # pandas 3.x defaults text columns to a PyArrow-backed string array, which
        # tries to materialize the entire ~2000-value block_readings column as one
        # contiguous Arrow buffer and raises ArrowMemoryError on ~150k+ rows. Force
        # plain-object dtype for this one column so pandas keeps it as a normal
        # Python string per cell instead.
        dtypes["block_readings"] = "object"

    df = pd.read_csv(csv_path, usecols=usecols, dtype=dtypes, nrows=nrows)
    timings["read_csv"] = time.time() - t0

    meta = df[metadata_columns].reset_index(drop=True)
    X_param = df[FEATURE_COLS].to_numpy(dtype=np.float32)

    source_key = _source_cache_key(csv_path, nrows)
    spatial_cache = _cache_path(f"{cache_tag}_{source_key}_spatial.npz")
    os.makedirs(CACHE_DIR, exist_ok=True)
    t1 = time.time()
    if os.path.exists(spatial_cache):
        X_spatial = np.load(spatial_cache)["spatial"]
        if X_spatial.shape != (len(df), 2):
            raise ValueError(f"invalid spatial cache shape in {spatial_cache}: {X_spatial.shape}")
    else:
        spatial_df = build_spatial_features(df)
        X_spatial = spatial_df.to_numpy(dtype=np.float32)
        np.savez_compressed(spatial_cache, spatial=X_spatial)
    timings["spatial_features"] = time.time() - t1

    X_block = None
    if need_blocks:
        block_cache = _cache_path(f"{cache_tag}_{source_key}_blocks.npz")
        t2 = time.time()
        if os.path.exists(block_cache):
            X_block = np.load(block_cache)["blocks"]
            if X_block.shape != (len(df), 2000):
                raise ValueError(f"invalid block cache shape in {block_cache}: {X_block.shape}")
        else:
            X_block = parse_block_readings(df["block_readings"])
            np.savez_compressed(block_cache, blocks=X_block)
        timings["parse_blocks"] = time.time() - t2

    return {
        "meta": meta,
        "X_param": X_param,
        "X_spatial": X_spatial,
        "X_block": X_block,
        "timings": timings,
    }
