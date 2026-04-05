"""JSON I/O helpers for Pydantic models."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def save_json(model: BaseModel, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(model.model_dump_json(indent=2), encoding="utf-8")
    return p


def load_json(model_class: type[T], path: str | Path) -> T:
    p = Path(path)
    data = json.loads(p.read_text(encoding="utf-8"))
    return model_class.model_validate(data)


def sha256_of_file(path: Path) -> str:
    """Compute SHA-256 hex digest of a file for provenance tracking."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
