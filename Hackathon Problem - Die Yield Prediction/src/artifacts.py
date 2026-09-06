from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def source_fingerprint(path: Path, version: str = "1") -> str:
    path = Path(path)
    stat = path.stat()
    payload = f"{path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}|{version}".encode()
    return hashlib.sha256(payload).hexdigest()[:20]


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
    os.replace(temporary, path)


def load_json(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)
