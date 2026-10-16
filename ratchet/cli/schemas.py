"""``ratchet schemas export``: the data-contract models as JSON Schema files."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from ratchet import models

__all__ = ["model_schemas", "export_schemas"]


def model_schemas() -> dict[str, str]:
    """``{file name: text}`` for every pydantic model exported by ``ratchet.models``."""
    out: dict[str, str] = {}
    for name in models.__all__:
        obj = getattr(models, name)
        if isinstance(obj, type) and issubclass(obj, BaseModel):
            schema = obj.model_json_schema()
            out[f"{name}.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return out


def export_schemas(dest: Path) -> list[Path]:
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, text in model_schemas().items():
        path = dest / name
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return written
