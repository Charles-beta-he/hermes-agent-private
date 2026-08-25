"""Fail-open, shadow-only request ledger and context lifecycle projection.

The functions in this module never replace the provider payload.  They measure
that payload and build a counterfactual copy which is written only as bounded
metadata to a profile-local JSONL ledger when explicitly enabled in
``config.yaml``::

    context_projection:
      enabled: true
      mode: shadow

Projection is deliberately conservative: a tool result must have a later
assistant message (evidence of one complete consumption opportunity), must not
be among the protected newest tool results, and must carry a usable reload
reference before the counterfactual copy is shortened.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import unquote, urlparse

from hermes_constants import get_hermes_home

try:
    import fcntl
except ImportError:  # pragma: no cover - Windows fails closed for ledger writes
    fcntl = None

logger = logging.getLogger(__name__)

_JSON_CONTENT_PREFIX = "\x00json:"
_PERSISTED_PATH_RE = re.compile(r"(?m)^Full output saved to:\s*(.+?)\s*$")
_PERSISTED_HASH_RE = re.compile(r"(?m)^Raw UTF-8 SHA-256 \(source\):\s*([a-fA-F0-9]+)\s*$")
_MAX_DURABLE_CANDIDATES = 100
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
_LEDGER_LOCK_TIMEOUT_SECONDS = 0.05
_LEDGER_LOCK_RETRY_SECONDS = 0.001
_CONFIG_CACHE: dict[str, tuple[tuple[int, int, int, int, int], dict[str, Any]]] = {}
_ARTIFACT_HASH_CACHE: dict[tuple[str, int, int, int, int, int], str] = {}

PROMPT_LEDGER_SCHEMA_VERSION = "PromptLedgerV1"
SHADOW_COVERAGE_SCHEMA_VERSION = "ShadowContextCoverageV1"
_COMPONENT_COUNTERS = (
    "stable_prompt_tokens",
    "context_prompt_tokens",
    "volatile_prompt_tokens",
    "active_tail_tokens",
    "compacted_history_tokens",
    "tool_result_inline_tokens",
    "tool_result_reference_count",
)
_USAGE_COUNTERS = (
    "fresh_input_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "output_tokens",
    "reasoning_tokens",
)
_TOTAL_TOKEN_COUNTERS = _USAGE_COUNTERS[:4]
_CALL_CLASSES = frozenset(
    {
        "user_response",
        "new_state",
        "new_evidence",
        "verification",
        "gate_escalation",
        "retry",
        "fallback",
        "compaction",
        "relay",
        "no_transition",
    }
)
_COVERAGE_SURFACES = (
    "objective",
    "non_goals",
    "constraints",
    "negative_requirements",
    "decision_lineage",
    "required_inputs",
    "artifact_evidence_refs",
    "authority_boundaries",
    "human_gates",
    "verification_commands",
    "budget",
    "resume_cursor",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _counter(value: Any, field: str, *, unknown_allowed: bool) -> int | None:
    if value is None and unknown_allowed:
        return None
    if type(value) is not int or value < 0:
        suffix = " or null" if unknown_allowed else ""
        raise ValueError(f"{field} must be a non-negative integer{suffix}")
    return value


def _required_ref(record: Mapping[str, Any], field: str) -> str:
    value = record.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty reference")
    return value


def build_prompt_ledger_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return one sanitized PromptLedgerV1 numeric/reference record."""

    component_input = record.get("component_counts")
    usage_input = record.get("usage")
    if not isinstance(component_input, Mapping) or not isinstance(usage_input, Mapping):
        raise ValueError("component_counts and usage must be mappings")
    call_class = record.get("call_class")
    if call_class not in _CALL_CLASSES:
        raise ValueError("call_class is not a PromptLedgerV1 call class")

    result = {
        "schema_version": PROMPT_LEDGER_SCHEMA_VERSION,
        "session_ref": _required_ref(record, "session_ref"),
        "turn_ref": _required_ref(record, "turn_ref"),
        "call_ref": _required_ref(record, "call_ref"),
        "prompt_manifest_hash": _required_ref(record, "prompt_manifest_hash"),
        "model_provider_ref": _required_ref(record, "model_provider_ref"),
        "provenance_ref": str(record.get("provenance_ref") or "unknown"),
        "source_ref": str(record.get("source_ref") or "unknown"),
        "component_counts": {
            field: _counter(component_input.get(field), field, unknown_allowed=False)
            for field in _COMPONENT_COUNTERS
        },
        "usage": {
            field: _counter(usage_input.get(field), field, unknown_allowed=True)
            for field in _USAGE_COUNTERS
        },
        "call_class": call_class,
    }
    for field in (
        "state_revision_before",
        "state_revision_after",
        "evidence_count_before",
        "evidence_count_after",
    ):
        result[field] = _counter(record.get(field), field, unknown_allowed=True)
    result["model_call_count"] = 1
    result["body_or_secret_stored"] = False
    return result


def append_prompt_ledger_record(
    records: Sequence[Mapping[str, Any]],
    record: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Pure append: return a new sequence and leave the input untouched."""

    if record.get("schema_version") != PROMPT_LEDGER_SCHEMA_VERSION:
        raise ValueError("record must be PromptLedgerV1")
    return [copy.deepcopy(dict(item)) for item in records] + [copy.deepcopy(dict(record))]


def _aggregate_prompt_records(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    components = {field: 0 for field in _COMPONENT_COUNTERS}
    usage = {field: 0 for field in _USAGE_COUNTERS}
    unknown = {field: 0 for field in _USAGE_COUNTERS}
    for record in records:
        for field in _COMPONENT_COUNTERS:
            components[field] += record["component_counts"][field]
        for field in _USAGE_COUNTERS:
            value = record["usage"][field]
            if value is None:
                unknown[field] += 1
            else:
                usage[field] += value
    total_tokens = None
    if not any(unknown[field] for field in _TOTAL_TOKEN_COUNTERS):
        total_tokens = sum(usage[field] for field in _TOTAL_TOKEN_COUNTERS)
    return {
        "record_count": len(records),
        "model_call_count": sum(record["model_call_count"] for record in records),
        "component_counts": components,
        "usage": usage,
        "usage_unknown_record_counts": unknown,
        "total_tokens": total_tokens,
    }


def _group_prompt_records(
    records: Sequence[Mapping[str, Any]], field: str
) -> list[dict[str, Any]]:
    values = sorted({str(record[field]) for record in records})
    return [
        {
            field: value,
            **_aggregate_prompt_records(
                [record for record in records if str(record[field]) == value]
            ),
        }
        for value in values
    ]


def export_prompt_ledger(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Export deterministic PromptLedgerV1 records and explicit aggregates."""

    normalized = [build_prompt_ledger_record(record) for record in records]
    return {
        "schema_version": "PromptLedgerExportV1",
        "records": normalized,
        "aggregate": _aggregate_prompt_records(normalized),
        "provenances": _group_prompt_records(normalized, "provenance_ref"),
        "sources": _group_prompt_records(normalized, "source_ref"),
        "models": _group_prompt_records(normalized, "model_provider_ref"),
    }


def canonical_prompt_ledger_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize an export or shadow report as canonical UTF-8 JSON bytes."""

    return _canonical_json(value).encode("utf-8")


def _coverage_present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (str, bytes, Sequence, Mapping)):
        return len(value) > 0
    return True


def evaluate_shadow_context_coverage(
    metadata: Mapping[str, Any],
    *,
    enabled: bool = False,
) -> dict[str, Any]:
    """Evaluate authorized metadata only; never construct or dispatch context."""

    disabled = {
        "schema_version": SHADOW_COVERAGE_SCHEMA_VERSION,
        "enabled": False,
        "evaluated": False,
        "advisory": True,
        "fail_closed": True,
        "dispatch_allowed": False,
    }
    if enabled is not True:
        return disabled
    coverage = {
        surface: _coverage_present(metadata.get(surface))
        for surface in _COVERAGE_SURFACES
    }
    missing = [surface for surface in _COVERAGE_SURFACES if not coverage[surface]]
    return {
        "schema_version": SHADOW_COVERAGE_SCHEMA_VERSION,
        "enabled": True,
        "evaluated": True,
        "advisory": True,
        "fail_closed": True,
        "dispatch_allowed": False,
        "coverage": coverage,
        "missing": missing,
        "complete": not missing,
    }


def _decode_content(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_JSON_CONTENT_PREFIX):
        try:
            return json.loads(value[len(_JSON_CONTENT_PREFIX) :])
        except (TypeError, ValueError):
            return value
    return value


def decode_stored_content(value: Any) -> Any:
    """Decode Hermes' NUL-prefixed JSON message sentinel without side effects."""
    return _decode_content(value)


def _content_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(_decode_content(value)).encode("utf-8")).hexdigest()


def _iter_content_parts(value: Any):
    decoded = _decode_content(value)
    if isinstance(decoded, list):
        yield from (part for part in decoded if isinstance(part, dict))


def _image_url(part: dict[str, Any]) -> str | None:
    if part.get("type") not in {"image", "image_url", "input_image"}:
        return None
    raw = part.get("image_url", part.get("url"))
    if isinstance(raw, dict):
        raw = raw.get("url")
    return raw if isinstance(raw, str) and raw else None


def _media_metrics(messages: list[dict[str, Any]]) -> dict[str, int]:
    parts = 0
    decoded_media_bytes = 0
    serialized_media_chars = 0
    for message in messages:
        content = _tool_result_content(message) if _is_tool_result(message) else message.get("content")
        for part in _iter_content_parts(content):
            url = _image_url(part)
            if not url:
                continue
            parts += 1
            serialized_media_chars += len(url)
            if url.startswith("data:") and ";base64," in url:
                payload = url.split(",", 1)[1]
                try:
                    decoded_media_bytes += len(base64.b64decode(payload, validate=False))
                except (ValueError, TypeError):
                    pass
    return {
        "parts": parts,
        "serialized_chars": serialized_media_chars,
        "decoded_media_bytes": decoded_media_bytes,
    }


def _serialized_chars(value: Any) -> int:
    return len(_canonical_json(value))


def _is_tool_result(message: dict[str, Any]) -> bool:
    return message.get("role") == "tool" or message.get("type") == "function_call_output"


def _tool_result_content(message: dict[str, Any]) -> Any:
    if message.get("type") == "function_call_output":
        return message.get("output")
    return message.get("content")


def _set_tool_result_content(message: dict[str, Any], value: Any) -> None:
    if message.get("type") == "function_call_output":
        message["output"] = value
    else:
        message["content"] = value


def _request_items(api_kwargs: dict[str, Any]) -> list[dict[str, Any]]:
    messages = api_kwargs.get("messages")
    if isinstance(messages, list):
        return messages
    responses_input = api_kwargs.get("input")
    return responses_input if isinstance(responses_input, list) else []


def measure_request(api_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return component sizes without retaining any raw request content."""
    messages = _request_items(api_kwargs)
    decoded_messages = []
    for message in messages:
        if isinstance(message, dict):
            item = dict(message)
            if _is_tool_result(item):
                _set_tool_result_content(item, _decode_content(_tool_result_content(item)))
            else:
                item["content"] = _decode_content(item.get("content"))
            decoded_messages.append(item)

    system = [message for message in decoded_messages if message.get("role") == "system"]
    tool_results = [message for message in decoded_messages if _is_tool_result(message)]
    other = [
        message
        for message in decoded_messages
        if message.get("role") != "system" and not _is_tool_result(message)
    ]
    tools = api_kwargs.get("tools")
    instructions = api_kwargs.get("instructions")
    return {
        "messages": {
            "count": len(decoded_messages),
            "serialized_chars": _serialized_chars(decoded_messages),
        },
        "stable_system": {
            "count": len(system),
            "serialized_chars": _serialized_chars(system),
        },
        "tool_results": {
            "count": len(tool_results),
            "serialized_chars": _serialized_chars(tool_results),
        },
        "active_history": {
            "count": len(other),
            "serialized_chars": _serialized_chars(other),
        },
        "tools": {
            "count": len(tools) if isinstance(tools, list) else 0,
            "serialized_chars": _serialized_chars(tools) if tools is not None else 0,
        },
        "instructions": {
            "serialized_chars": _serialized_chars(instructions) if instructions is not None else 0,
        },
        "multimodal": _media_metrics(decoded_messages),
    }


def _safe_preview(text: str, limit: int) -> str:
    preview = text[: max(0, limit)]
    try:
        from agent.redact import redact_sensitive_text

        preview = redact_sensitive_text(preview, force=True)
    except Exception:
        pass
    preview = re.sub(r"\bsk-[A-Za-z0-9_-]{16,}\b", "[REDACTED]", preview)
    preview = re.sub(
        r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*\S+",
        lambda match: f"{match.group(1)}=[REDACTED]",
        preview,
    )
    return preview


def _bounded_metadata(value: Any, limit: int = 128) -> str:
    return _safe_preview(str(value or ""), limit)


def _metadata_sha256(value: Any, namespace: str) -> str:
    """Return a domain-separated digest; durable records never store raw metadata."""
    raw = str(value or "").encode("utf-8", errors="replace")
    return hashlib.sha256(namespace.encode("ascii") + b"\0" + raw).hexdigest()


def _text_from_content(content: Any) -> str:
    decoded = _decode_content(content)
    if isinstance(decoded, str):
        return decoded
    if isinstance(decoded, list):
        chunks = [
            str(part.get("text", ""))
            for part in decoded
            if isinstance(part, dict) and part.get("type") == "text"
        ]
        return "\n".join(chunk for chunk in chunks if chunk)
    return ""


def _local_file_from_url(url: str) -> Path | None:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if not parsed.scheme and url.startswith("/"):
        return Path(url)
    return None


def _multimodal_reload_refs(content: Any) -> tuple[list[dict[str, str]] | None, str | None]:
    refs: list[dict[str, str]] = []
    saw_media = False
    for part in _iter_content_parts(content):
        url = _image_url(part)
        if not url:
            continue
        saw_media = True
        if url.startswith("data:"):
            return None, "inline_media"
        local_path = _local_file_from_url(url)
        if local_path is None:
            return None, "remote_reference_unverified"
        try:
            source_hash = _artifact_sha256(str(local_path))
        except OSError:
            return None, "unsafe_artifact"
        refs.append({"url": url, "sha256": source_hash})
    if not saw_media or not refs:
        return None, "missing_reference"
    return refs, None


def _open_readonly_without_symlinks(source: Path) -> int:
    """Open an absolute regular-file candidate without following path-component symlinks."""
    if not source.is_absolute() or any(part in {".", ".."} for part in source.parts):
        raise OSError("artifact path must be absolute and normalized")
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    if not nofollow:
        raise OSError("secure artifact hashing is unsupported on this platform")
    directory_flags = (
        os.O_RDONLY
        | int(getattr(os, "O_DIRECTORY", 0))
        | nofollow
        | int(getattr(os, "O_CLOEXEC", 0))
    )
    current_fd = os.open(source.anchor, directory_flags)
    try:
        for component in source.parts[1:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        file_flags = (
            os.O_RDONLY
            | nofollow
            | int(getattr(os, "O_CLOEXEC", 0))
            | int(getattr(os, "O_NONBLOCK", 0))
        )
        result = os.open(source.name, file_flags, dir_fd=current_fd)
        return result
    finally:
        os.close(current_fd)


def _artifact_sha256(path: str) -> str:
    source = Path(path)
    before = source.lstat()
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise OSError("artifact must be a real regular file")
    if before.st_size > _MAX_ARTIFACT_BYTES:
        raise OSError("artifact exceeds the shadow hashing size limit")
    cache_key = (
        str(source),
        before.st_dev,
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    cached = _ARTIFACT_HASH_CACHE.get(cache_key)
    if cached is not None:
        return cached

    fd = _open_readonly_without_symlinks(source)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise OSError("artifact changed before hashing")
        digest = hashlib.sha256()
        bytes_read = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, _MAX_ARTIFACT_BYTES + 1 - bytes_read))
            if not chunk:
                break
            bytes_read += len(chunk)
            if bytes_read > _MAX_ARTIFACT_BYTES:
                raise OSError("artifact grew beyond the shadow hashing size limit")
            digest.update(chunk)
        after = os.fstat(fd)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
        ):
            raise OSError("artifact changed while hashing")
    finally:
        os.close(fd)

    result = digest.hexdigest()
    _ARTIFACT_HASH_CACHE[cache_key] = result
    if len(_ARTIFACT_HASH_CACHE) > 64:
        _ARTIFACT_HASH_CACHE.pop(next(iter(_ARTIFACT_HASH_CACHE)))
    return result


def _persisted_reload_ref(content: str) -> tuple[str | None, str | None, str | None]:
    path_match = _PERSISTED_PATH_RE.search(content)
    if not path_match:
        return None, None, "missing_reference"
    path = path_match.group(1).strip()
    artifact_path = Path(path).expanduser()
    if not artifact_path.is_absolute():
        return None, None, "relative_artifact_reference"
    path = str(artifact_path)
    try:
        path_stat = artifact_path.lstat()
    except OSError:
        return None, None, "missing_artifact"
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return None, None, "unsafe_artifact"
    hash_match = _PERSISTED_HASH_RE.search(content)
    source_hash = hash_match.group(1).lower() if hash_match else None
    if source_hash is None or len(source_hash) != 64:
        return None, source_hash, "missing_valid_source_hash"
    try:
        actual_hash = _artifact_sha256(path)
    except OSError:
        return None, source_hash, "artifact_read_error"
    if actual_hash != source_hash:
        return None, source_hash, "source_hash_mismatch"
    return path, source_hash, None


def _shadow_envelope(
    *,
    kind: str,
    tool_name: str,
    tool_call_id: str,
    original_chars: int,
    digest: str,
    preview: str,
    reload_ref: Any,
) -> str:
    return _canonical_json(
        {
            "hermes_context_projection": "shadow_candidate_v1",
            "kind": kind,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            "original_chars": original_chars,
            "content_sha256": digest,
            "preview": preview,
            "reload_ref": reload_ref,
        }
    )


def project_messages_shadow(
    messages: list[dict[str, Any]],
    *,
    protect_last_tool_results: int = 1,
    min_text_chars: int = 4096,
    preview_chars: int = 256,
) -> dict[str, Any]:
    """Build a recoverability-gated counterfactual copy of ``messages``."""
    projected = copy.deepcopy(messages)
    tool_indices = [index for index, message in enumerate(messages) if _is_tool_result(message)]
    protected = set(tool_indices[-max(0, protect_last_tool_results) :]) if protect_last_tool_results else set()
    candidates: list[dict[str, Any]] = []

    for index in tool_indices:
        if index in protected:
            continue
        message = messages[index]
        consumed = any(item.get("role") == "assistant" for item in messages[index + 1 :])
        if not consumed:
            continue
        content = _tool_result_content(message)
        decoded = _decode_content(content)
        original_chars = _serialized_chars(decoded)
        tool_call_id = str(message.get("tool_call_id") or message.get("call_id") or "")
        tool_name = str(message.get("tool_name") or message.get("name") or "")
        digest = _content_hash(decoded)
        kind = ""
        reload_ref: Any = None
        source_hash: str | None = None
        retirement_blocker: str | None = None

        if any(_image_url(part) for part in _iter_content_parts(decoded)):
            kind = "multimodal_tool_result"
            reload_ref, retirement_blocker = _multimodal_reload_refs(decoded)
        elif isinstance(decoded, str) and len(decoded) >= min_text_chars:
            kind = "text_tool_result"
            reload_ref, source_hash, retirement_blocker = _persisted_reload_ref(decoded)
        else:
            continue

        recoverable = bool(reload_ref)
        candidate = {
            "message_index": index,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "kind": kind,
            "original_chars": original_chars,
            "content_sha256": digest,
            "source_sha256": source_hash,
            "recoverable": recoverable,
            "would_apply": False,
            "retirement_blocker": retirement_blocker,
        }
        candidates.append(candidate)
        if not recoverable:
            continue

        envelope = _shadow_envelope(
            kind=kind,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            original_chars=original_chars,
            digest=digest,
            preview=_safe_preview(_text_from_content(decoded), preview_chars),
            reload_ref=reload_ref,
        )
        if _serialized_chars(envelope) >= original_chars:
            candidate["retirement_blocker"] = "no_size_reduction"
            continue
        candidate["would_apply"] = True
        candidate["retirement_blocker"] = None
        _set_tool_result_content(projected[index], envelope)

    original_chars = _serialized_chars(messages)
    projected_chars = _serialized_chars(projected)
    return {
        "original_chars": original_chars,
        "projected_chars": projected_chars,
        "estimated_reduction_chars": max(0, original_chars - projected_chars),
        "candidates": candidates,
        "projected_messages": projected,
    }


def _durable_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    """Return the bounded, path-free candidate subset allowed in JSONL."""
    return {
        "message_index": int(candidate.get("message_index", -1)),
        "tool_call_id_sha256": _metadata_sha256(candidate.get("tool_call_id"), "tool_call_id"),
        "tool_name_sha256": _metadata_sha256(candidate.get("tool_name"), "tool_name"),
        "kind": _bounded_metadata(candidate.get("kind"), 64),
        "original_chars": int(candidate.get("original_chars", 0)),
        "content_sha256": _bounded_metadata(candidate.get("content_sha256"), 64),
        "source_sha256": _bounded_metadata(candidate.get("source_sha256"), 64),
        "recoverable": candidate.get("recoverable") is True,
        "would_apply": candidate.get("would_apply") is True,
        "retirement_blocker": _bounded_metadata(candidate.get("retirement_blocker"), 64),
    }


def _load_projection_config_file() -> dict[str, Any]:
    """Read only this feature's YAML stanza without runtime config side effects."""
    path = get_hermes_home() / "config.yaml"
    try:
        source = path.stat()
        cache_key = str(path)
        signature = (
            source.st_dev,
            source.st_ino,
            source.st_size,
            source.st_mtime_ns,
            source.st_ctime_ns,
        )
        cached = _CONFIG_CACHE.get(cache_key)
        if cached and cached[0] == signature:
            return cached[1]

        import yaml

        parsed = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(parsed, dict):
            return {}
        projection = parsed.get("context_projection")
        result = {"context_projection": projection} if isinstance(projection, dict) else {}
        _CONFIG_CACHE[cache_key] = (signature, result)
        if len(_CONFIG_CACHE) > 4:
            _CONFIG_CACHE.pop(next(iter(_CONFIG_CACHE)))
        return result
    except Exception:
        return {}


def _projection_config(config: dict[str, Any] | None) -> dict[str, Any] | None:
    if config is None:
        config = _load_projection_config_file()
    raw = config.get("context_projection") if isinstance(config, dict) else None
    if not isinstance(raw, dict) or raw.get("enabled") is not True:
        return None
    mode = raw.get("mode")
    if type(mode) is not str or mode.strip().lower() != "shadow":
        return None
    return raw


def _bounded_config_int(
    config: dict[str, Any],
    key: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    value = config.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"context_projection.{key} must be an integer in [{minimum}, {maximum}]")
    return value


def _ledger_path(agent: Any, ledger_dir: Path | str | None) -> Path:
    root = Path(ledger_dir) if ledger_dir is not None else get_hermes_home() / "prompt_ledgers"
    session_digest = _metadata_sha256(
        getattr(agent, "session_id", None) or "unknown", "ledger_session"
    )[:32]
    return root / f"{session_digest}.jsonl"


def _open_private_ledger_root(root: Path) -> int:
    """Open/create an absolute directory without following any path-component symlink."""
    if not root.is_absolute() or any(part in {".", ".."} for part in root.parts):
        raise PermissionError("prompt ledger root must be an absolute canonical path")
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    directory = int(getattr(os, "O_DIRECTORY", 0))
    if not nofollow or not directory:
        raise OSError("secure prompt ledger directory traversal is unsupported on this platform")
    flags = os.O_RDONLY | directory | nofollow | int(getattr(os, "O_CLOEXEC", 0))
    current_fd = os.open(root.anchor, flags)
    try:
        for component in root.parts[1:]:
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                next_fd = os.open(component, flags, dir_fd=current_fd)
            os.close(current_fd)
            current_fd = next_fd
        root_stat = os.fstat(current_fd)
        if not stat.S_ISDIR(root_stat.st_mode):
            raise PermissionError("prompt ledger root must be a real directory")
        if hasattr(os, "geteuid") and root_stat.st_uid != os.geteuid():
            raise PermissionError("prompt ledger root must be owned by the current user")
        if stat.S_IMODE(root_stat.st_mode) & 0o077:
            raise PermissionError("prompt ledger root must not grant group/world permissions")
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    root = path.parent
    payload = (_canonical_json(record) + "\n").encode("utf-8")
    nofollow = int(getattr(os, "O_NOFOLLOW", 0))
    if not nofollow or fcntl is None:
        raise OSError("secure prompt ledger append is unsupported on this platform")
    common_flags = nofollow | int(getattr(os, "O_CLOEXEC", 0))
    root_fd = _open_private_ledger_root(root)
    try:
        fd = -1
        for attempt in range(3):
            try:
                fd = os.open(
                    path.name,
                    os.O_APPEND
                    | os.O_CREAT
                    | os.O_WRONLY
                    | int(getattr(os, "O_NONBLOCK", 0))
                    | common_flags,
                    0o600,
                    dir_fd=root_fd,
                )
                break
            except FileNotFoundError:
                if attempt == 2:
                    raise
                os.fstat(root_fd)
        locked = False
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                raise PermissionError("prompt ledger target must be a regular file")
            if file_stat.st_nlink != 1:
                raise PermissionError("prompt ledger target must not be hard-linked")
            if hasattr(os, "geteuid") and file_stat.st_uid != os.geteuid():
                raise PermissionError("prompt ledger target must be owned by the current user")
            if stat.S_IMODE(file_stat.st_mode) & 0o077:
                raise PermissionError("prompt ledger target must be private")
            deadline = time.monotonic() + _LEDGER_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("prompt ledger lock acquisition timed out")
                    time.sleep(_LEDGER_LOCK_RETRY_SECONDS)
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short prompt ledger write")
                view = view[written:]
        finally:
            if locked:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
            os.close(fd)
    finally:
        os.close(root_fd)


def _provider_usage(
    response: Any,
    *,
    provider: str | None = None,
    api_mode: str | None = None,
) -> dict[str, int]:
    usage = response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    if isinstance(usage, dict):
        from types import SimpleNamespace

        usage = SimpleNamespace(
            **{
                key: SimpleNamespace(**value) if isinstance(value, dict) else value
                for key, value in usage.items()
            }
        )
    from agent.usage_pricing import normalize_usage

    canonical = normalize_usage(usage, provider=provider, api_mode=api_mode)
    return {
        "prompt_tokens": canonical.prompt_tokens,
        "completion_tokens": canonical.output_tokens,
        "total_tokens": canonical.total_tokens,
        "input_tokens": canonical.input_tokens,
        "output_tokens": canonical.output_tokens,
        "cache_read_tokens": canonical.cache_read_tokens,
        "cache_write_tokens": canonical.cache_write_tokens,
        "reasoning_tokens": canonical.reasoning_tokens,
    }


def call_with_prompt_ledger(
    agent: Any,
    api_kwargs: dict[str, Any],
    call: Callable[[dict[str, Any]], Any],
    *,
    config: dict[str, Any] | None = None,
    ledger_dir: Path | str | None = None,
) -> Any:
    """Call the provider with the original object and observe it fail-open."""
    try:
        resolved = _projection_config(config)
    except Exception:
        logger.debug("prompt ledger config failed; calling provider unchanged", exc_info=True)
        return call(api_kwargs)

    if resolved is None:
        return call(api_kwargs)

    try:
        request_id = uuid.uuid4().hex
        path = _ledger_path(agent, ledger_dir)
        projection = project_messages_shadow(
            _request_items(api_kwargs),
            protect_last_tool_results=_bounded_config_int(
                resolved, "protect_last_tool_results", 1, minimum=0, maximum=10_000
            ),
            min_text_chars=_bounded_config_int(
                resolved, "min_text_chars", 4096, minimum=1, maximum=100_000_000
            ),
            preview_chars=_bounded_config_int(
                resolved, "preview_chars", 256, minimum=0, maximum=16_384
            ),
        )
        durable_candidates = [
            _durable_candidate(item)
            for item in projection["candidates"][-_MAX_DURABLE_CANDIDATES:]
        ]
        request_record = {
            "schema_version": 1,
            "event": "request",
            "request_id": request_id,
            "timestamp": _utc_now(),
            "session_id_sha256": _metadata_sha256(getattr(agent, "session_id", ""), "session_id"),
            "provider_sha256": _metadata_sha256(getattr(agent, "provider", ""), "provider"),
            "model_sha256": _metadata_sha256(getattr(agent, "model", ""), "model"),
            "projection_mode": "shadow",
            "provider_payload_mutated": False,
            "request_metrics": measure_request(api_kwargs),
            "projection": {
                "original_chars": projection["original_chars"],
                "projected_chars": projection["projected_chars"],
                "estimated_reduction_chars": projection["estimated_reduction_chars"],
                "candidate_count": len(projection["candidates"]),
                "recoverable_candidate_count": sum(
                    1 for item in projection["candidates"] if item["recoverable"]
                ),
                "candidate_records_truncated": max(
                    0, len(projection["candidates"]) - len(durable_candidates)
                ),
                "candidates": durable_candidates,
            },
        }
    except Exception:
        logger.debug("prompt ledger preparation failed; calling provider unchanged", exc_info=True)
        return call(api_kwargs)

    try:
        _append_jsonl(path, request_record)
    except Exception:
        logger.debug("prompt ledger request write failed", exc_info=True)

    try:
        response = call(api_kwargs)
    except BaseException as exc:
        try:
            _append_jsonl(
                path,
                {
                    "schema_version": 1,
                    "event": "error",
                    "request_id": request_id,
                    "timestamp": _utc_now(),
                    "error_type": type(exc).__name__,
                },
            )
        except BaseException:
            logger.debug("prompt ledger error write failed", exc_info=True)
        raise

    try:
        _append_jsonl(
            path,
            {
                "schema_version": 1,
                "event": "response",
                "request_id": request_id,
                "timestamp": _utc_now(),
                "provider_usage": _provider_usage(
                    response,
                    provider=str(getattr(agent, "provider", "") or ""),
                    api_mode=str(getattr(agent, "api_mode", "") or ""),
                ),
            },
        )
    except Exception:
        logger.debug("prompt ledger response write failed", exc_info=True)
    return response
