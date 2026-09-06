import numpy as np
import warnings

from src.block_features import BLOCK_STAT_COLUMNS, block_statistics, fit_block_pca


def test_block_statistics_return_requested_features():
    blocks = np.array([[0, 1, 2, 3, 4], [2, 2, 2, 2, 2]], dtype=np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        stats = block_statistics(blocks, rolling_window=3, tail_count=2)
    assert list(stats.columns) == BLOCK_STAT_COLUMNS
    assert stats.loc[0, "block_mean"] == 2.0
    assert stats.loc[0, "mean_lowest_20"] == 0.5
    assert stats.loc[0, "mean_highest_20"] == 3.5
    assert np.isfinite(stats.to_numpy()).all()


def test_pca_fits_only_training_indices():
    train = np.array([[0, 0], [2, 2]], dtype=np.float32)
    valid = np.array([[100, 100]], dtype=np.float32)
    transform = fit_block_pca(train, n_components=1, random_state=42)
    assert np.allclose(transform.scaler.mean_, [1, 1])
    assert transform.transform(valid).shape == (1, 1)
