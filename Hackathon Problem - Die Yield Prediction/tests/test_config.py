from pathlib import Path

from src.config import EXPERIMENT_IDS, ProjectPaths


def test_project_paths_keep_inputs_separate_from_outputs(tmp_path):
    paths = ProjectPaths(tmp_path)
    assert paths.input_dir == tmp_path / "input"
    assert paths.cache_dir == tmp_path / "cache"
    assert paths.results_dir == tmp_path / "results"
    assert paths.input_dir not in paths.cache_dir.parents
    assert {"A1", "A2", "A3", "A4", "B1", "B2", "AE32", "AE64", "B3a", "B3b", "B4", "B5", "B6"}.issubset(EXPERIMENT_IDS)
