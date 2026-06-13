"""Typed packet helpers for the workflow kernel runtime boundary."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from workflow_kernel.io import canonical_json_bytes

SENSITIVE_FIELD_PARTS = ("key", "token", "password", "secret", "credential", "connection_string")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            key = str(k)
            if any(part in key.lower() for part in SENSITIVE_FIELD_PARTS):
                out[key] = "[REDACTED]"
            else:
                out[key] = redact_secrets(v)
        return out
    if isinstance(value, list):
        return [redact_secrets(v) for v in value]
    return value


def digest_payload(payload: Any) -> str:
    safe_payload = redact_secrets(payload)
    return hashlib.sha256(canonical_json_bytes(safe_payload)).hexdigest()


def contract_packet(*, packet_type: str, payload: dict[str, Any], evidence_refs: list[str] | None = None) -> dict[str, Any]:
    safe_payload = redact_secrets(payload)
    return {
        "packet_type": packet_type,
        "created_at": utc_now(),
        "payload": safe_payload,
        "payload_digest": digest_payload(payload),
        "evidence_refs": evidence_refs or [],
    }
