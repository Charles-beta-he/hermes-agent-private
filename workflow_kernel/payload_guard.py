"""Payload guard for enterprise workflow-kernel unattended dispatch."""
from __future__ import annotations

import hashlib
import re
from typing import Any

PAYLOAD_GUARD_SCHEMA = "workflow_kernel_enterprise_payload_guard_v1"
MAX_PAYLOAD_CHARS = 500
BLOCK_PATTERNS = {
    "secret_like": re.compile(r"(?i)(api[_-]?key|token|password|secret|credential|connection[_-]?string)\s*[:=]"),
    "private_key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "absolute_private_path": re.compile(r"/Users/charles/(?!\.hermes/hermes-agent/docs/private/workflow-kernel-open-gate)"),
}
ALLOWED_PURPOSES = {"workflow_kernel_status_smoke", "workflow_kernel_enterprise_heartbeat"}


def classify_payload(payload: str, *, purpose: str) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if purpose not in ALLOWED_PURPOSES:
        errors.append(f"purpose_not_allowed:{purpose}")
    if not payload.strip():
        errors.append("payload_empty")
    if len(payload) > MAX_PAYLOAD_CHARS:
        errors.append("payload_too_long")
    for name, pattern in BLOCK_PATTERNS.items():
        if pattern.search(payload):
            errors.append(f"blocked_pattern:{name}")
    if "http://" in payload or "https://" in payload:
        warnings.append("payload_contains_url")
    return {
        "schema": PAYLOAD_GUARD_SCHEMA,
        "status": "PASS" if not errors else "FAIL",
        "purpose": purpose,
        "payload_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "length": len(payload),
        "errors": errors,
        "warnings": warnings,
        "dynamic_payload_allowed": not errors,
    }


__all__ = ["PAYLOAD_GUARD_SCHEMA", "classify_payload"]
