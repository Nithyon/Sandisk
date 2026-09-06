from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from .artifacts import atomic_json, source_fingerprint
from .config import BLOCK_WIDTH, PARAMETRIC_FEATURE_COUNT
from .spatial_features import SPATIAL_COLUMNS, compute_spatial_features


_FEATURE_PATTERN = re.compile(r"^feature_(\d+)$")


def feature_columns(columns: Iterable[str]) -> list[str]:
    numbered = [(int(match.group(1)), column) for column in columns if (match := _FEATURE_PATTERN.match(column))]
    return [column for _, column in sorted(numbered)]


def validate_schema(columns: Iterable[str], require_label: bool, expected_features: int = PARAMETRIC_FEATURE_COUNT) -> list[str]:
    columns = list(columns)
    required = {"wafer_id", "die_row", "die_col", "old_label", "block_readings"}
    if require_label:
        required.add("label")
    missing = required.difference(columns)
    if missing:
        raise ValueError(f"missing required columns: {sorted(missing)}")
    features = feature_columns(columns)
    expected = [f"feature_{i}" for i in range(1, expected_features + 1)]
    if features != expected:
        raise ValueError(f"parametric features must be contiguous feature_1 through feature_{expected_features}")
    return features


def parse_block_column(values: pd.Series, expected_width: int = BLOCK_WIDTH) -> np.ndarray:
    rows: list[np.ndarray] = []
    for row_number, raw in enumerate(values.astype(str)):
        parsed = np.fromstring(raw, sep=" ", dtype=np.float32)
        if parsed.size != expected_width:
            raise ValueError(f"block row {row_number} has {parsed.size} values; expected {expected_width}")
        rows.append(parsed)
    if not rows:
        return np.empty((0, expected_width), dtype=np.float32)
    return np.vstack(rows).astype(np.float32, copy=False)


def _validated_binary(values: pd.Series, name: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    raw = numeric.to_numpy(dtype=np.float64)
    if not np.isfinite(raw).all() or not np.isin(raw, (0.0, 1.0)).all():
        raise ValueError(f"{name} must contain only integer 0 or 1")
    return numeric.astype(np.int8)


def _validated_int16(values: pd.Series, name: str) -> pd.Series:
    numeric = pd.to_numeric(values, errors="coerce")
    raw = numeric.to_numpy(dtype=np.float64)
    limits = np.iinfo(np.int16)
    if (
        not np.isfinite(raw).all()
        or not np.equal(raw, np.floor(raw)).all()
        or (raw < limits.min).any()
        or (raw > limits.max).any()
    ):
        raise ValueError(f"{name} must contain int16-range integer values")
    return numeric.astype(np.int16)


def count_csv_rows(path: Path, buffer_size: int = 64 * 1024 * 1024) -> int:
    line_count = 0
    last_byte = b""
    with Path(path).open("rb") as handle:
        while chunk := handle.read(buffer_size):
            line_count += chunk.count(b"\n")
            last_byte = chunk[-1:]
    if last_byte and last_byte != b"\n":
        line_count += 1
    return max(0, line_count - 1)


def load_cache(cache_dir: Path, mmap_mode: str = "r") -> tuple[pd.DataFrame, np.ndarray]:
    cache_dir = Path(cache_dir)
    macro = pd.read_parquet(cache_dir / "model_a.parquet")
    blocks = np.load(cache_dir / "blocks.float32.npy", mmap_mode=mmap_mode)
    if len(macro) != len(blocks):
        raise ValueError("cache row alignment failure")
    return macro, blocks


def prepare_cache(
    source_csv: Path,
    cache_dir: Path,
    *,
    require_label: bool,
    expected_features: int = PARAMETRIC_FEATURE_COUNT,
    block_width: int = BLOCK_WIDTH,
    chunksize: int = 1000,
    force: bool = False,
) -> dict[str, object]:
    source_csv = Path(source_csv).resolve()
    cache_dir = Path(cache_dir).resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = cache_dir / "metadata.json"
    fingerprint = source_fingerprint(source_csv, version="spatial-v1-block-v1")
    if not force and metadata_path.exists() and (cache_dir / "model_a.parquet").exists() and (cache_dir / "blocks.float32.npy").exists():
        from .artifacts import load_json

        metadata = load_json(metadata_path)
        if metadata.get("fingerprint") == fingerprint:
            return metadata

    row_count = count_csv_rows(source_csv)
    temp_blocks = cache_dir / "blocks.float32.tmp.npy"
    final_blocks = cache_dir / "blocks.float32.npy"
    temp_macro = cache_dir / "model_a.tmp.parquet"
    final_macro = cache_dir / "model_a.parquet"
    blocks = np.lib.format.open_memmap(temp_blocks, mode="w+", dtype=np.float32, shape=(row_count, block_width))
    macro_parts: list[pd.DataFrame] = []
    cursor = 0
    started = time.perf_counter()
    features: list[str] | None = None
    try:
        for chunk in pd.read_csv(source_csv, chunksize=chunksize):
            if features is None:
                features = validate_schema(chunk.columns, require_label, expected_features)
            parsed = parse_block_column(chunk["block_readings"], block_width)
            end = cursor + len(chunk)
            if end > row_count:
                raise ValueError("CSV grew while cache was being prepared")
            blocks[cursor:end] = parsed
            compact = chunk.drop(columns=["block_readings"]).copy()
            compact[features] = compact[features].astype(np.float32)
            compact["die_row"] = _validated_int16(compact["die_row"], "die_row")
            compact["die_col"] = _validated_int16(compact["die_col"], "die_col")
            compact["old_label"] = _validated_binary(compact["old_label"], "old_label")
            if require_label:
                compact["label"] = _validated_binary(compact["label"], "label")
            macro_parts.append(compact)
            cursor = end
        if cursor != row_count:
            raise ValueError(f"row count changed during preprocessing: counted {row_count}, parsed {cursor}")
        blocks.flush()
        del blocks
        macro = pd.concat(macro_parts, ignore_index=True)
        if macro.duplicated(["wafer_id", "die_row", "die_col"]).any():
            raise ValueError("duplicate die identifiers found")
        if not set(macro["old_label"].unique()).issubset({0, 1}):
            raise ValueError("old_label must contain only 0 and 1")
        if require_label and not set(macro["label"].unique()).issubset({0, 1}):
            raise ValueError("label must contain only 0 and 1")

        spatial = compute_spatial_features(macro[["wafer_id", "die_row", "die_col", "old_label"]])
        for column in SPATIAL_COLUMNS:
            macro[column] = spatial[column].to_numpy()
        ordered = ["wafer_id", *SPATIAL_COLUMNS, *features, "old_label"]
        if require_label:
            ordered.append("label")
        macro = macro[ordered]
        macro.to_parquet(temp_macro, index=False, compression="zstd")
        os.replace(temp_macro, final_macro)
        os.replace(temp_blocks, final_blocks)
        metadata: dict[str, object] = {
            "source": str(source_csv),
            "fingerprint": fingerprint,
            "rows": row_count,
            "eligible_rows": int(macro["old_label"].eq(0).sum()),
            "wafers": int(macro["wafer_id"].nunique()),
            "features": features,
            "spatial_features": SPATIAL_COLUMNS,
            "block_width": block_width,
            "dtype": "float32",
            "has_label": require_label,
            "seconds": round(time.perf_counter() - started, 3),
        }
        atomic_json(metadata_path, metadata)
        return metadata
    except Exception:
        try:
            del blocks
        except UnboundLocalError:
            pass
        for temporary in (temp_blocks, temp_macro):
            temporary.unlink(missing_ok=True)
        raise
