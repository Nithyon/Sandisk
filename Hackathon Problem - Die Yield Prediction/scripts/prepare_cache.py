from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.config import ProjectPaths
from src.data_loader import prepare_cache


def main() -> None:
    parser = argparse.ArgumentParser(description="Create compact die-yield feature caches without modifying source CSVs.")
    parser.add_argument("--split", choices=("train", "validation", "test"), required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--chunksize", type=int, default=1000)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--final-test", action="store_true", help="Required to inspect/cache labeled test.csv.")
    args = parser.parse_args()
    if args.split == "test" and not args.final_test:
        parser.error("test.csv is sealed; pass --final-test only after model and threshold are frozen")
    paths = ProjectPaths(args.project_root)
    paths.ensure_output_dirs()
    source = paths.input_dir / f"{args.split}.csv"
    metadata = prepare_cache(
        source,
        paths.cache_dir / args.split,
        require_label=args.split in {"train", "test"},
        chunksize=args.chunksize,
        force=args.force,
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
