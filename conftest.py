"""Repository-wide pytest path setup for the nested benchmark package."""

import os
import sys
from pathlib import Path


BENCHMARK_ROOT = Path(__file__).resolve().parent / "Hackathon Problem - Die Yield Prediction"
sys.path.insert(0, str(BENCHMARK_ROOT))
existing_pythonpath = os.environ.get("PYTHONPATH")
os.environ["PYTHONPATH"] = (
    str(BENCHMARK_ROOT)
    if not existing_pythonpath
    else os.pathsep.join((str(BENCHMARK_ROOT), existing_pythonpath))
)
