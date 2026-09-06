import numpy as np
import pandas as pd

from src.spatial_features import SPATIAL_COLUMNS, compute_spatial_features


def test_spatial_context_uses_old_failures_before_eligibility_filter():
    wafer = pd.DataFrame(
        {
            "wafer_id": ["W1"] * 5,
            "die_row": [0, 0, 1, 1, 2],
            "die_col": [0, 1, 0, 1, 1],
            "old_label": [1, 0, 0, 0, 0],
        }
    )
    result = compute_spatial_features(wafer, zone_rows=4, zone_cols=4, window=5)
    eligible = result.loc[wafer.old_label.eq(0)]
    assert np.allclose(eligible["wafer_old_fail_rate"], 0.2)
    assert np.allclose(eligible["neighborhood_old_fail_rate"], 0.2)
    assert eligible["nearest_old_fail_distance"].min() == 1.0
    assert list(result.columns) == SPATIAL_COLUMNS


def test_neighborhood_denominator_counts_only_actual_dies():
    wafer = pd.DataFrame(
        {
            "wafer_id": ["W1"] * 3,
            "die_row": [0, 0, 1],
            "die_col": [0, 1, 0],
            "old_label": [1, 0, 0],
        }
    )
    result = compute_spatial_features(wafer, window=5)
    assert np.allclose(result["neighborhood_old_fail_rate"], 1 / 3)


def test_no_old_failure_has_finite_distance_and_indicator():
    wafer = pd.DataFrame(
        {
            "wafer_id": ["W1", "W1"],
            "die_row": [0, 2],
            "die_col": [0, 2],
            "old_label": [0, 0],
        }
    )
    result = compute_spatial_features(wafer)
    assert np.isfinite(result["nearest_old_fail_distance"]).all()
    assert result["has_old_failure"].eq(0).all()
    assert result["radial_distance"].between(0, 1).all()
    assert result["zone_id"].between(0, 15).all()
