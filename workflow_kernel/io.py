"""Shared JSON artifact IO for workflow-kernel compatibility CLIs."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def write_json_artifact(path: str | Path, payload: Any) -> dict[str, str]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = canonical_json_bytes(payload)
    target.write_bytes(data)
    return {"path": str(target), "sha256": hashlib.sha256(data).hexdigest()}


def read_json_artifact(path: str | Path, *, missing_ok: bool = False) -> Any:
    target = Path(path)
    try:
        return json.loads(target.read_text())
    except FileNotFoundError:
        if missing_ok:
            return {"missing": True, "path": str(target)}
        raise
