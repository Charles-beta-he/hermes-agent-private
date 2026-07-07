"""Prompt manifest hashing and drift checks for Hermes system prompts.

PromptManifestV1 is intentionally computed outside the assembled prompt text so
hash pinning can be recorded in run metadata without perturbing cacheable prompt
prefixes.  Callers may also append the manifest as a non-cacheable content block
for providers that support structured system-content blocks.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = "PromptManifestV1"
PROMPT_ID = "hermes-core-system-prompt"
SEMANTIC_VERSION = "1.0.0"
DEFAULT_SOURCE_FILES = (
    "agent/prompt_builder.py",
    "agent/system_prompt.py",
    "tools/kanban_tools.py",
    "cron/scheduler.py",
)


def _sha256_json(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _parts_for(agent: Any) -> dict[str, str]:
    parts = getattr(agent, "_cached_system_prompt_parts", None)
    if isinstance(parts, Mapping):
        return {
            "stable": str(parts.get("stable") or ""),
            "context": str(parts.get("context") or ""),
            "volatile": str(parts.get("volatile") or ""),
        }
    prompt = str(getattr(agent, "_cached_system_prompt", "") or "")
    return {"stable": prompt, "context": "", "volatile": ""}


def build_prompt_manifest(
    agent: Any,
    *,
    source_files: Sequence[str] | None = None,
    prompt_id: str = PROMPT_ID,
    semantic_version: str = SEMANTIC_VERSION,
) -> dict[str, Any]:
    """Return a stable PromptManifestV1 for an assembled Hermes prompt."""

    parts = _parts_for(agent)
    tool_names = sorted(str(t) for t in (getattr(agent, "valid_tool_names", None) or []))
    toolsets = sorted(str(t) for t in (getattr(agent, "enabled_toolsets", None) or []))
    source_list = [str(Path(p).as_posix()) for p in (source_files or DEFAULT_SOURCE_FILES)]
    content_fingerprint = {
        "parts": parts,
        "platform": str(getattr(agent, "platform", "") or ""),
        "tools": tool_names,
        "toolsets": toolsets,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "prompt_id": prompt_id,
        "semantic_version": semantic_version,
        "content_hash": _sha256_json(content_fingerprint),
        "source_files": source_list,
        "skill_set_hash": _sha256_json(tool_names),
        "platform_profile": str(getattr(agent, "platform", "") or ""),
        "toolset_profile": toolsets,
    }
    manifest["manifest_hash"] = _sha256_json(manifest)
    return manifest


def _stable_manifest_json(manifest: Mapping[str, Any]) -> str:
    return json.dumps(dict(manifest), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def prompt_manifest_metadata(agent: Any | None = None) -> dict[str, Any]:
    """Return prompt pinning metadata for run/session/trajectory records."""

    manifest = None
    if agent is not None:
        existing = getattr(agent, "_prompt_manifest_v1", None)
        if isinstance(existing, Mapping):
            manifest = dict(existing)
    if manifest is None:
        try:
            raw = __import__("os").environ.get("HERMES_PROMPT_MANIFEST_JSON", "")
            parsed = json.loads(raw) if raw else None
            if isinstance(parsed, Mapping):
                manifest = dict(parsed)
        except Exception:
            manifest = None
    if not manifest:
        prompt_hash = None
        if agent is not None:
            prompt_hash = getattr(agent, "_prompt_hash", None)
        if not prompt_hash:
            prompt_hash = __import__("os").environ.get("HERMES_PROMPT_HASH", "")
        return {"prompt_hash": prompt_hash} if prompt_hash else {}
    metadata = {"prompt_manifest": manifest}
    content_hash = manifest.get("content_hash")
    if content_hash:
        metadata["prompt_hash"] = content_hash
    return metadata


def attach_prompt_manifest_metadata(agent: Any) -> dict[str, Any]:
    """Build/reuse the prompt manifest and attach it to common metadata sinks."""

    existing = getattr(agent, "_prompt_manifest_v1", None)
    manifest = dict(existing) if isinstance(existing, Mapping) else build_prompt_manifest(agent)
    agent._prompt_manifest_v1 = manifest
    agent._prompt_hash = manifest.get("content_hash")

    import os
    os.environ["HERMES_PROMPT_MANIFEST_JSON"] = _stable_manifest_json(manifest)
    os.environ["HERMES_PROMPT_HASH"] = str(manifest.get("content_hash") or "")

    session_config = getattr(agent, "_session_init_model_config", None)
    if isinstance(session_config, dict):
        session_config.setdefault("prompt_hash", manifest.get("content_hash"))
        session_config.setdefault("prompt_manifest", manifest)
    return manifest


def render_prompt_manifest_block(manifest: Mapping[str, Any]) -> str:
    """Render manifest as a compact non-cacheable system block."""

    return "PromptManifestV1: " + _stable_manifest_json(manifest)


def check_prompt_manifest_drift(
    baseline: Mapping[str, Any],
    current: Mapping[str, Any],
) -> dict[str, Any]:
    """Compare two manifests and report hash/source/schema drift."""

    watched = [
        "schema_version",
        "prompt_id",
        "semantic_version",
        "content_hash",
        "source_files",
        "skill_set_hash",
        "platform_profile",
        "toolset_profile",
        "manifest_hash",
    ]
    changed = [key for key in watched if baseline.get(key) != current.get(key)]
    return {
        "drifted": bool(changed),
        "changed_fields": changed,
        "baseline_hash": baseline.get("manifest_hash"),
        "current_hash": current.get("manifest_hash"),
    }
