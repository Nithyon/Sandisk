from __future__ import annotations

import argparse
from pathlib import Path

from src.config import EXPERIMENT_IDS, ProjectPaths, load_overrides
from src.runner import format_summary, run_cross_validation


def main() -> None:
    parser = argparse.ArgumentParser(description="Run leakage-safe wafer-grouped die-yield cross-validation.")
    parser.add_argument("--experiment", choices=sorted(EXPERIMENT_IDS), required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--device", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--overrides", type=Path, help="JSON object with explicit model/training overrides.")
    parser.add_argument("--save-fold-models", action="store_true", help="Uses extra disk; off by default.")
    args = parser.parse_args()
    result = run_cross_validation(
        args.experiment,
        ProjectPaths(args.project_root),
        device=args.device,
        overrides=load_overrides(args.overrides),
        n_splits=args.folds,
        save_fold_models=args.save_fold_models,
    )
    print(format_summary(result))


if __name__ == "__main__":
    main()
