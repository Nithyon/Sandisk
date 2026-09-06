import pandas as pd
import pytest

from src.data_loader import count_csv_rows, feature_columns, load_cache, parse_block_column, prepare_cache, validate_schema


def test_feature_columns_are_numeric_order_not_lexical():
    columns = ["feature_10", "feature_2", "feature_1"]
    assert feature_columns(columns) == ["feature_1", "feature_2", "feature_10"]


def test_block_parser_enforces_width_and_float32():
    parsed = parse_block_column(pd.Series(["1 2 3", "4 5 6"]), expected_width=3)
    assert parsed.shape == (2, 3)
    assert parsed.dtype.name == "float32"
    with pytest.raises(ValueError, match="expected 3"):
        parse_block_column(pd.Series(["1 2"]), expected_width=3)


def test_schema_requires_complete_parametric_feature_sequence():
    columns = ["wafer_id", "die_row", "die_col", "feature_1", "feature_3", "old_label", "label", "block_readings"]
    with pytest.raises(ValueError, match="contiguous"):
        validate_schema(columns, require_label=True, expected_features=3)


def test_row_count_handles_a_csv_without_trailing_newline(tmp_path):
    path = tmp_path / "small.csv"
    path.write_text("header\nfirst\nsecond", encoding="utf-8")
    assert count_csv_rows(path) == 2


def test_prepare_cache_keeps_full_wafer_spatial_context_and_row_alignment(tmp_path):
    source = tmp_path / "tiny.csv"
    pd.DataFrame(
        {
            "wafer_id": ["W1", "W1", "W1"],
            "die_row": [0, 0, 1],
            "die_col": [0, 1, 0],
            "feature_1": [1.0, 2.0, 3.0],
            "feature_2": [4.0, 5.0, 6.0],
            "old_label": [1, 0, 0],
            "label": [1, 1, 0],
            "block_readings": ["1 2 3", "4 5 6", "7 8 9"],
        }
    ).to_csv(source, index=False)
    metadata = prepare_cache(
        source, tmp_path / "cache", require_label=True, expected_features=2, block_width=3, chunksize=2
    )
    macro, blocks = load_cache(tmp_path / "cache")
    assert metadata["rows"] == 3
    assert blocks.shape == (3, 3)
    assert macro.loc[1, "wafer_old_fail_rate"] == pytest.approx(1 / 3)
    assert blocks[2].tolist() == [7.0, 8.0, 9.0]


def test_prepare_cache_rejects_fractional_binary_labels_before_casting(tmp_path):
    source = tmp_path / "invalid.csv"
    pd.DataFrame(
        {
            "wafer_id": ["W1"],
            "die_row": [0],
            "die_col": [0],
            "feature_1": [1.0],
            "old_label": [0.5],
            "label": [1],
            "block_readings": ["1 2"],
        }
    ).to_csv(source, index=False)
    with pytest.raises(ValueError, match="old_label must contain only integer 0 or 1"):
        prepare_cache(source, tmp_path / "cache", require_label=True, expected_features=1, block_width=2)
