"""Durable task-centric multi-session control plane.

The store is deliberately provider- and entrypoint-neutral.  CLI, Telegram,
delegated workers, and closure sessions exchange immutable packets through one
SQLite SSOT; natural-language conversation summaries are never authoritative.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import os
import re
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import time
import uuid
from typing import Any, Iterator, Mapping, Sequence


SCHEMA = "hermes_task_runtime_v1"
CONTEXT_PACKET_SCHEMA = "hermes_context_packet_v1"
WORKER_RESULT_SCHEMA = "hermes_worker_result_v1"
TERMINAL_SLICE_STATES = {"completed", "cancelled"}
TASK_CLASSES = {
    "bugfix",
    "complex",
    "cross_surface_journey",
    "frozen_design",
    "high_risk_architecture",
    "mature_capability",
    "product_design",
    "reference_reimplementation",
}
DISCOVERY_LANES = {"project_evidence", "prototype", "competitor", "open_source"}
PREFLIGHT_STATUSES = {"completed", "not_applicable"}
OPEN_SOURCE_EVALUATION_DIMENSIONS = {
    "real_closure",
    "license",
    "maintenance",
    "security",
    "technical_fit",
    "data_ownership",
    "version_pin",
    "rollback",
    "lifecycle_cost",
}
_SEMVER = (
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
_EXACT_VERSION_PIN = re.compile(
    rf"^(?:"
    rf"v?{_SEMVER}"
    rf"|[^\s=]+==v?{_SEMVER}"
    rf"|version:v?{_SEMVER}"
    r"|tag:[0-9A-Za-z][0-9A-Za-z._-]*"
    r"|commit:(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})"
    r"|(?:[^@\s]+@)?sha256:[0-9a-fA-F]{64}"
    r")$"
)


def _is_exact_version_pin(value: str) -> bool:
    normalized = value.strip()
    if not _EXACT_VERSION_PIN.fullmatch(normalized):
        return False
    lowered = normalized.lower()
    return not any(
        lowered.endswith(f":{moving}")
        for moving in ("latest", "main", "master", "head", "dev", "nightly")
    )


STRICT_AUTHORITATIVE_SOURCE_KINDS = {
    "business_ssot",
    "project_ssot",
    "prd",
    "original_requirement",
    "approved_prototype",
    "prototype",
    "screenshot",
    "video",
    "reference_source",
    "runtime_contract",
}
STRICT_NON_AUTHORITATIVE_SOURCE_KINDS = {
    "agent_summary",
    "derived_index",
    "derived_summary",
    "generated",
    "model_agreement",
    "open_source_demo",
    "readme",
    "summary",
    "test_output",
    "test_result",
    "research_note",
    "inference",
}
STRICT_SOURCE_KINDS = (
    STRICT_AUTHORITATIVE_SOURCE_KINDS | STRICT_NON_AUTHORITATIVE_SOURCE_KINDS
)
STRICT_UI_OBLIGATION_FIELDS = {
    "pages",
    "states",
    "interactions",
    "viewports",
    "negative_cases",
    "fidelity_dimensions",
}


_MAX_CANDIDATE_ARCHIVE_BYTES = 256 * 1024 * 1024


def _read_candidate_archive_bytes(archive: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(archive, flags)
    except OSError as exc:
        raise ValueError("workspace_candidate_archive_invalid") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("workspace_candidate_archive_invalid")
        if metadata.st_size < 0 or metadata.st_size > _MAX_CANDIDATE_ARCHIVE_BYTES:
            raise ValueError("workspace_candidate_archive_size_invalid")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(_MAX_CANDIDATE_ARCHIVE_BYTES + 1)
        if len(content) != metadata.st_size or len(content) > _MAX_CANDIDATE_ARCHIVE_BYTES:
            raise ValueError("workspace_candidate_archive_size_invalid")
        return content
    finally:
        os.close(descriptor)


def _candidate_archive_manifest(
    archive: Path, *, archive_bytes: bytes | None = None
) -> tuple[dict[str, Any], str]:
    if archive_bytes is None:
        archive_bytes = _read_candidate_archive_bytes(archive)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as bundle:
            members = bundle.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)) or "MANIFEST.json" not in names:
                raise ValueError("workspace_candidate_archive_members_invalid")
            manifest_member = bundle.getmember("MANIFEST.json")
            if not manifest_member.isfile():
                raise ValueError("workspace_candidate_manifest_invalid")
            manifest_file = bundle.extractfile(manifest_member)
            if manifest_file is None:
                raise ValueError("workspace_candidate_manifest_invalid")
            manifest = json.loads(manifest_file.read().decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("workspace_candidate_manifest_invalid")
            paths = [str(item) for item in manifest.get("changed_artifacts") or []]
            deleted = {str(item) for item in manifest.get("deleted_artifacts") or []}
            if (
                paths != sorted(paths)
                or len(paths) != len(set(paths))
                or not deleted.issubset(paths)
                or any(
                    not path
                    or Path(path).is_absolute()
                    or ".." in Path(path).parts
                    or Path(path).as_posix() != path
                    for path in paths
                )
            ):
                raise ValueError("workspace_candidate_manifest_paths_invalid")
            expected_members = {"MANIFEST.json"} | {
                f"files/{path}" for path in paths if path not in deleted
            }
            if set(names) != expected_members:
                raise ValueError("workspace_candidate_archive_members_invalid")
            digest = hashlib.sha256()
            for path in paths:
                if path in deleted:
                    digest.update(b"D\0")
                    digest.update(path.encode("utf-8"))
                    digest.update(b"\0" + b"0" + b"\0")
                    continue
                member = bundle.getmember(f"files/{path}")
                if member.issym():
                    if member.mode != 0o777:
                        raise ValueError("workspace_candidate_archive_mode_invalid")
                    mode = b"120000"
                    content = member.linkname.encode("utf-8")
                elif member.isfile():
                    if member.mode not in (0o644, 0o755):
                        raise ValueError("workspace_candidate_archive_mode_invalid")
                    mode = b"100755" if member.mode == 0o755 else b"100644"
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        raise ValueError("workspace_candidate_archive_member_invalid")
                    content = extracted.read()
                else:
                    raise ValueError("workspace_candidate_archive_member_invalid")
                digest.update(mode + b"\0")
                digest.update(path.encode("utf-8") + b"\0")
                digest.update(str(len(content)).encode("ascii") + b"\0")
                digest.update(content)
            return manifest, "sha256:" + digest.hexdigest()
    except (OSError, KeyError, tarfile.TarError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("workspace_candidate_manifest_invalid") from exc


def _verify_workspace_candidate_receipt(
    receipt: Mapping[str, Any] | None,
    *,
    task_id: str,
    slice_id: str,
    packet_id: str,
    lease_token: str,
    source_repo: str,
    base_commit: str,
    candidate_digest: str,
    changed_artifacts: set[str],
) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ValueError("workspace_candidate_receipt_required")
    payload = dict(receipt)
    allowed = {
        "schema", "task_id", "slice_id", "packet_id", "lease_token",
        "source_repo", "base_commit", "candidate_digest", "changed_artifacts", "archive_path",
        "archive_sha256", "workspace_reap_receipt", "observed_at",
    }
    if set(payload) != allowed or payload.get("schema") != "hermes_workspace_candidate_receipt_v1":
        raise ValueError("workspace_candidate_receipt_invalid")
    expected = (
        task_id, slice_id, packet_id, lease_token, source_repo, base_commit,
        candidate_digest,
    )
    actual = tuple(
        str(payload.get(key) or "")
        for key in (
            "task_id", "slice_id", "packet_id", "lease_token", "source_repo",
            "base_commit", "candidate_digest",
        )
    )
    if actual != expected:
        raise ValueError("workspace_candidate_receipt_binding_mismatch")
    if {str(item) for item in payload.get("changed_artifacts") or []} != changed_artifacts:
        raise ValueError("workspace_candidate_write_set_mismatch")
    reap = payload.get("workspace_reap_receipt")
    if not isinstance(reap, Mapping) or not (
        reap.get("removed") is True
        and reap.get("residual_probe_succeeded") is True
        and not reap.get("residual_writer_pids")
        and str(reap.get("packet_id") or "") == packet_id
        and str(reap.get("lease_token") or "") == lease_token
    ):
        raise ValueError("workspace_candidate_reap_invalid")
    root = (
        Path(os.environ.get("HERMES_HOME", "~/.hermes")).expanduser().resolve()
        / "task-runtime-workspaces" / "candidates"
    )
    archive = Path(str(payload.get("archive_path") or "")).expanduser().absolute()
    if archive.parent.resolve() != root or not archive.is_file() or archive.is_symlink():
        raise ValueError("workspace_candidate_archive_invalid")
    archive_bytes = _read_candidate_archive_bytes(archive)
    archive_sha = hashlib.sha256(archive_bytes).hexdigest()
    if archive_sha != str(payload.get("archive_sha256") or ""):
        raise ValueError("workspace_candidate_archive_digest_mismatch")
    manifest, archive_candidate_digest = _candidate_archive_manifest(
        archive, archive_bytes=archive_bytes
    )
    if archive_candidate_digest != candidate_digest:
        raise ValueError("workspace_candidate_content_digest_mismatch")
    if not isinstance(manifest, dict) or (
        manifest.get("schema") != "hermes_workspace_candidate_manifest_v1"
        or str(manifest.get("task_id") or "") != task_id
        or str(manifest.get("slice_id") or "") != slice_id
        or str(manifest.get("packet_id") or "") != packet_id
        or str(manifest.get("lease_token") or "") != lease_token
        or str(manifest.get("source_repo") or "") != source_repo
        or str(manifest.get("base_commit") or "") != base_commit
        or str(manifest.get("candidate_digest") or "") != candidate_digest
        or {str(item) for item in manifest.get("changed_artifacts") or []}
        != changed_artifacts
    ):
        raise ValueError("workspace_candidate_manifest_binding_mismatch")
    return payload


def _git_bytes(
    repo: Path,
    args: Sequence[str],
    *,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
) -> bytes:
    merged_env = os.environ.copy()
    if env:
        merged_env.update({str(key): str(value) for key, value in env.items()})
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *args],
            input=stdin,
            capture_output=True,
            check=True,
            env=merged_env,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("workspace_candidate_git_integration_failed") from exc
    return completed.stdout


def _integrate_workspace_candidate_ref(
    receipt: Mapping[str, Any], *, integrator_session_id: str
) -> dict[str, Any]:
    source_repo = Path(str(receipt["source_repo"])).expanduser().resolve()
    if str(source_repo) != str(receipt["source_repo"]) or not (source_repo / ".git").exists():
        raise ValueError("workspace_candidate_source_repo_invalid")
    base_commit = str(receipt["base_commit"])
    resolved_base = _git_bytes(source_repo, ["rev-parse", "--verify", f"{base_commit}^{{commit}}"])
    resolved_base_text = resolved_base.decode("ascii").strip()
    if resolved_base_text != base_commit:
        raise ValueError("workspace_candidate_base_commit_mismatch")
    current_head = _git_bytes(source_repo, ["rev-parse", "--verify", "HEAD"]).decode(
        "ascii"
    ).strip()
    if current_head != base_commit:
        raise ValueError("workspace_candidate_source_head_mismatch")
    archive = Path(str(receipt["archive_path"])).expanduser().absolute()
    archive_bytes = _read_candidate_archive_bytes(archive)
    if hashlib.sha256(archive_bytes).hexdigest() != str(receipt["archive_sha256"]):
        raise ValueError("workspace_candidate_archive_digest_mismatch")
    manifest, computed_digest = _candidate_archive_manifest(
        archive, archive_bytes=archive_bytes
    )
    if computed_digest != str(receipt["candidate_digest"]):
        raise ValueError("workspace_candidate_content_digest_mismatch")
    for key in (
        "task_id", "slice_id", "packet_id", "lease_token", "source_repo",
        "base_commit", "candidate_digest",
    ):
        if str(manifest.get(key) or "") != str(receipt[key]):
            raise ValueError("workspace_candidate_manifest_binding_mismatch")
    binding = "\0".join(
        str(receipt[key])
        for key in ("task_id", "slice_id", "packet_id", "lease_token", "candidate_digest")
    )
    ref_suffix = hashlib.sha256(binding.encode("utf-8")).hexdigest()
    candidate_ref = f"refs/hermes/candidates/{ref_suffix}"
    index_fd, index_name = tempfile.mkstemp(prefix="hermes-candidate-index-")
    os.close(index_fd)
    Path(index_name).unlink(missing_ok=True)
    index_env = {"GIT_INDEX_FILE": index_name}
    try:
        _git_bytes(source_repo, ["read-tree", base_commit], env=index_env)
        deleted = {str(item) for item in manifest.get("deleted_artifacts") or []}
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as bundle:
            for path in [str(item) for item in manifest["changed_artifacts"]]:
                if path in deleted:
                    _git_bytes(
                        source_repo,
                        ["update-index", "--force-remove", "--", path],
                        env=index_env,
                    )
                    continue
                member = bundle.getmember(f"files/{path}")
                if member.issym():
                    mode = "120000"
                    content = member.linkname.encode("utf-8")
                else:
                    mode = "100755" if member.mode == 0o755 else "100644"
                    extracted = bundle.extractfile(member)
                    if extracted is None:
                        raise ValueError("workspace_candidate_archive_member_invalid")
                    content = extracted.read()
                object_id = _git_bytes(
                    source_repo, ["hash-object", "-w", "--stdin"], stdin=content
                ).decode("ascii").strip()
                _git_bytes(
                    source_repo,
                    ["update-index", "--add", "--cacheinfo", mode, object_id, path],
                    env=index_env,
                )
        tree_id = _git_bytes(source_repo, ["write-tree"], env=index_env).decode("ascii").strip()
    finally:
        Path(index_name).unlink(missing_ok=True)
    observed_at = str(receipt["observed_at"])
    commit_env = {
        "GIT_AUTHOR_NAME": "Hermes Candidate Controller",
        "GIT_AUTHOR_EMAIL": "hermes-candidate@localhost",
        "GIT_COMMITTER_NAME": "Hermes Candidate Controller",
        "GIT_COMMITTER_EMAIL": "hermes-candidate@localhost",
        "GIT_AUTHOR_DATE": observed_at,
        "GIT_COMMITTER_DATE": observed_at,
    }
    message = (
        "Hermes managed workspace candidate\n\n"
        f"Task: {receipt['task_id']}\n"
        f"Slice: {receipt['slice_id']}\n"
        f"Packet: {receipt['packet_id']}\n"
        f"Candidate: {receipt['candidate_digest']}\n"
    ).encode("utf-8")
    commit_id = _git_bytes(
        source_repo,
        ["commit-tree", tree_id, "-p", base_commit],
        env=commit_env,
        stdin=message,
    ).decode("ascii").strip()
    try:
        existing_ref = _git_bytes(
            source_repo, ["rev-parse", "--verify", candidate_ref]
        ).decode("ascii").strip()
    except ValueError:
        existing_ref = ""
    if existing_ref:
        if existing_ref != commit_id:
            raise ValueError("workspace_candidate_ref_conflict")
        idempotent = True
    else:
        _git_bytes(
            source_repo,
            ["update-ref", candidate_ref, commit_id, "0" * 40],
        )
        idempotent = False
    observed_paths = {
        line
        for line in _git_bytes(
            source_repo,
            ["diff-tree", "--no-commit-id", "--name-only", "-r", commit_id],
        ).decode("utf-8").splitlines()
        if line
    }
    expected_paths = {str(item) for item in manifest["changed_artifacts"]}
    if observed_paths != expected_paths:
        raise ValueError("workspace_candidate_ref_write_set_mismatch")
    return {
        "schema": "hermes_workspace_candidate_integration_v1",
        "source_repo": str(source_repo),
        "base_commit": base_commit,
        "candidate_ref": candidate_ref,
        "candidate_commit": commit_id,
        "candidate_tree": tree_id,
        "candidate_digest": str(receipt["candidate_digest"]),
        "integrator_session_id": integrator_session_id,
        "idempotent_replay": idempotent,
    }


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _legacy_event_recorded_at(task_created_at: Any, ordinal: int) -> str:
    if type(task_created_at) in {int, float} and math.isfinite(task_created_at):
        try:
            created = datetime.fromtimestamp(task_created_at, timezone.utc)
        except (OverflowError, OSError, ValueError) as exc:
            raise ValueError("legacy_task_created_at_invalid") from exc
    elif type(task_created_at) is str and task_created_at.strip():
        try:
            created = datetime.fromisoformat(task_created_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("legacy_task_created_at_invalid") from exc
    else:
        raise ValueError("legacy_task_created_at_invalid")
    if created.tzinfo is None:
        raise ValueError("legacy_task_created_at_invalid")
    return (
        created.astimezone(timezone.utc) + timedelta(microseconds=ordinal)
    ).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _execute_sql_script_transactionally(
    conn: sqlite3.Connection, script: str
) -> None:
    """Execute complete SQL statements without sqlite3.executescript's implicit commit."""
    statement = ""
    for character in script:
        statement += character
        if character != ";" or not sqlite3.complete_statement(statement):
            continue
        sql = statement.strip()
        if sql:
            conn.execute(sql)
        statement = ""
    if statement.strip():
        raise ValueError("schema_script_incomplete")


def _source_event_identity_json(value: Any) -> str:
    """Return canonical JSON without accepting Python-to-JSON type coercion."""

    def validate(item: Any) -> None:
        if item is None or type(item) in {bool, int, str}:
            return
        if type(item) is float:
            if not math.isfinite(item):
                raise ValueError("source_event_payload_invalid")
            return
        if type(item) is list:
            for child in item:
                validate(child)
            return
        if type(item) is dict:
            for key, child in item.items():
                if type(key) is not str:
                    raise ValueError("source_event_payload_invalid")
                validate(child)
            return
        raise ValueError("source_event_payload_invalid")

    validate(value)
    return _json(value)


def _source_event_content_present(content: Any) -> bool:
    """Treat native scalar values as semantic content, including False and zero."""
    return not (
        content is None
        or (type(content) is str and not content)
        or (type(content) in {list, dict} and not content)
    )


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _decode_source_event_collection(value: str) -> Any:
    """Decode authoritative legacy JSON without collapsing duplicate members."""

    def object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        decoded: dict[str, Any] = {}
        for key, item in pairs:
            if key in decoded:
                raise ValueError("source_event_payload_invalid")
            decoded[key] = item
        return decoded

    def reject_non_finite_number(_value: str) -> Any:
        raise ValueError("source_event_payload_invalid")

    return json.loads(
        value,
        object_pairs_hook=object_without_duplicate_keys,
        parse_constant=reject_non_finite_number,
    )


def _validate_original_sources(sources: Any, *, allow_empty: bool = False) -> list[dict[str, Any]]:
    if type(sources) is not list or (not sources and not allow_empty):
        raise ValueError("original_sources_required")
    identifiers: set[str] = set()
    for item in sources:
        if type(item) is not dict:
            raise ValueError("original_source_payload_invalid")
        source_id = item.get("source_id")
        if type(source_id) is not str or not source_id.strip():
            raise ValueError("original_source_identity_invalid")
        if source_id in identifiers:
            raise ValueError("original_source_identity_duplicate")
        content = item.get("content")
        if type(content) is not str or not content:
            raise ValueError("original_source_content_invalid")
        identifiers.add(source_id)
    _source_event_identity_json(sources)
    return sources


def _source_record_identity(item: Mapping[str, Any]) -> str:
    value = item.get("source_id") if "source_id" in item else item.get("event_id")
    if type(value) is not str or not value.strip():
        raise ValueError("task_source_identity_invalid")
    return value


def _validate_source_event_source_ref(
    payload: Mapping[str, Any], source_ids: Collection[str]
) -> None:
    if "source_id" not in payload:
        return
    source_id = payload.get("source_id")
    if not _native_identity(source_id):
        raise ValueError("source_event_source_identity_invalid")
    if source_id not in source_ids:
        raise ValueError("source_event_source_ref_unknown")


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _strict_ui_contract(spec: Mapping[str, Any]) -> dict[str, Any]:
    governance = spec.get("governance")
    if not isinstance(governance, Mapping) or governance.get("target_profile") != "strict_ui":
        return {}
    manifest = governance.get("source_obligations")
    if not isinstance(manifest, Mapping):
        return {}
    obligation_refs = sorted(
        f"{field}:{value}"
        for field, values in manifest.items()
        for value in (values if isinstance(values, list) else [])
    )
    oracle_refs = list(governance.get("oracle_refs") or [])
    fidelity_dimensions = list(manifest.get("fidelity_dimensions") or [])
    return {
        "obligation_refs": obligation_refs,
        "obligation_digest": _digest(obligation_refs),
        "oracle_refs": oracle_refs,
        "fidelity_dimensions": fidelity_dimensions,
    }


def _strict_ui_claim_contracts(spec: Mapping[str, Any]) -> dict[str, dict[str, list[Any]]]:
    if not _strict_ui_contract(spec):
        return {}
    contracts: dict[str, dict[str, list[Any]]] = {}
    for field in ("required_clauses", "negative_requirements"):
        for item in spec.get(field) or []:
            if not isinstance(item, Mapping):
                continue
            claim_id = item.get("id")
            if type(claim_id) is not str or not claim_id.strip():
                continue
            contracts[claim_id] = {
                key: list(item.get(key) or [])
                for key in (
                    "obligation_refs",
                    "oracle_refs",
                    "fidelity_dimensions",
                    "source_anchors",
                )
            }
    return contracts


def _native_identity(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _native_identity_list(value: Any) -> bool:
    return (
        type(value) is list
        and all(_native_identity(item) for item in value)
        and len(value) == len(set(value))
    )


_NATIVE_JSON_MAX_DEPTH = 100
_NATIVE_JSON_MAX_NODES = 10_000


def _non_native_json_paths(
    value: Any,
    path: str = "$",
    *,
    _stack: set[int] | None = None,
    _nodes: list[int] | None = None,
    _depth: int = 0,
) -> list[str]:
    stack = set() if _stack is None else _stack
    nodes = [0] if _nodes is None else _nodes
    nodes[0] += 1
    if nodes[0] > _NATIVE_JSON_MAX_NODES:
        return [f"{path}:max_nodes"] if nodes[0] == _NATIVE_JSON_MAX_NODES + 1 else []

    findings: list[str] = []
    value_type = type(value)
    if value is None or value_type in (bool, int, str):
        return findings
    if value_type is float:
        if not math.isfinite(value):
            findings.append(f"{path}:non_finite_float")
        return findings
    if isinstance(value, (dict, list)):
        if value_type not in (dict, list):
            return [f"{path}:{value_type.__qualname__}"]
        if _depth >= _NATIVE_JSON_MAX_DEPTH:
            return [f"{path}:max_depth"]
        object_id = id(value)
        if object_id in stack:
            return [f"{path}:cycle"]
        stack.add(object_id)
        try:
            if type(value) is dict:
                for index, (key, item) in enumerate(value.items()):
                    if type(key) is not str:
                        findings.append(
                            f"{path}.<key:{index}>:{type(key).__qualname__}"
                        )
                        child_path = f"{path}.<value:{index}>"
                    else:
                        child_path = f"{path}.{key}"
                    findings.extend(
                        _non_native_json_paths(
                            item,
                            child_path,
                            _stack=stack,
                            _nodes=nodes,
                            _depth=_depth + 1,
                        )
                    )
            else:
                if value_type is not list:
                    findings.append(f"{path}:{value_type.__qualname__}")
                for index, item in enumerate(value):
                    findings.extend(
                        _non_native_json_paths(
                            item,
                            f"{path}[{index}]",
                            _stack=stack,
                            _nodes=nodes,
                            _depth=_depth + 1,
                        )
                    )
        finally:
            stack.remove(object_id)
        return findings
    findings.append(f"{path}:{value_type.__qualname__}")
    return findings


def _native_json_value(value: Any) -> bool:
    return not _non_native_json_paths(value)


def _claim_evidence_contract_matches(
    item: Mapping[str, Any], contracts: Mapping[str, Mapping[str, Sequence[Any]]]
) -> bool:
    claim_id = item.get("claim_id")
    if not _native_identity(claim_id):
        return False
    claim_key = str(claim_id)
    if claim_key not in contracts:
        return False
    references_match = all(
        _native_identity_list(item.get(key))
        and set(item[key]) == set(contracts[claim_key][key])
        for key in ("obligation_refs", "oracle_refs", "fidelity_dimensions")
    )
    anchors = item.get("source_anchors")
    return (
        references_match
        and type(anchors) is list
        and anchors == list(contracts[claim_key]["source_anchors"])
    )


def _task_id() -> str:
    return f"task_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:10]}"


def _result_id(packet_id: str, worker_session_id: str, result: Mapping[str, Any]) -> str:
    return "result_" + _digest(
        {"packet_id": packet_id, "worker_session_id": worker_session_id, "result": dict(result)}
    ).split(":", 1)[1][:24]


class RevisionConflict(RuntimeError):
    pass


def _runtime_owner_profile(explicit: str | None = None) -> str:
    owner = str(explicit or os.environ.get("HERMES_PROFILE") or "").strip()
    if owner:
        return owner
    home_raw = str(os.environ.get("HERMES_HOME") or "").strip()
    if not home_raw:
        return "default"
    home = Path(home_raw).expanduser().resolve()
    default_home = (Path.home() / ".hermes").resolve()
    if home == default_home:
        return "default"
    if home.parent == (default_home / "profiles") and home.name:
        return home.name
    identity = hashlib.sha256(str(home).encode("utf-8")).hexdigest()[:24]
    return f"home:{identity}"


class TaskRuntimeStore:
    """SQLite SSOT for tasks, bindings, slices, packets, results, and closure."""

    def __init__(self, path: str | Path, *, owner_profile: str | None = None):
        self.path = Path(path).expanduser()
        self.owner_profile = _runtime_owner_profile(owner_profile)
        if not self.owner_profile:
            raise ValueError("owner_profile_required")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    def _ensure_wal_mode(self) -> None:
        deadline = time.monotonic() + 5.0
        delay = 0.005
        while True:
            conn = sqlite3.connect(str(self.path), timeout=0.05)
            try:
                mode = str(conn.execute("PRAGMA journal_mode = WAL").fetchone()[0]).lower()
                if mode == "wal":
                    return
                raise sqlite3.OperationalError(f"journal_mode_not_wal:{mode}")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                    raise
            finally:
                conn.close()
            time.sleep(delay)
            delay = min(delay * 2.0, 0.1)

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._write() as conn:
            tasks_exist = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tasks'"
            ).fetchone()
            if tasks_exist is not None:
                legacy_columns = {
                    row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()
                }
                source_ids_by_task: dict[str, set[str]] = {}
                legacy_event_identities: dict[tuple[str, str], str] = {}
                if "original_sources_json" in legacy_columns:
                    for existing_task in conn.execute(
                        "SELECT task_id, original_sources_json FROM tasks"
                    ).fetchall():
                        try:
                            existing_sources = _decode_source_event_collection(
                                existing_task["original_sources_json"]
                            )
                            _validate_original_sources(existing_sources, allow_empty=True)
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ValueError("original_source_payload_invalid") from exc
                        source_ids_by_task[existing_task["task_id"]] = {
                            _source_record_identity(item) for item in existing_sources
                        }
                if "source_events_json" in legacy_columns:
                    for existing_task in conn.execute(
                        "SELECT task_id, source_events_json FROM tasks"
                    ).fetchall():
                        try:
                            legacy_events = _decode_source_event_collection(
                                existing_task["source_events_json"]
                            )
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ValueError("source_event_collection_invalid") from exc
                        if type(legacy_events) is not list:
                            raise ValueError("source_event_collection_invalid")
                        event_ids: set[str] = set()
                        for event in legacy_events:
                            if type(event) is not dict:
                                raise ValueError("source_event_invalid")
                            event_id = event.get("event_id")
                            if not _native_identity(event_id):
                                raise ValueError("source_event_identity_invalid")
                            assert type(event_id) is str
                            event_key = event_id
                            if event_key in event_ids:
                                raise ValueError("source_event_id_conflict")
                            if event_key in source_ids_by_task.get(
                                existing_task["task_id"], set()
                            ):
                                raise ValueError("task_source_identity_invalid")
                            event_ids.add(event_key)
                            _validate_source_event_source_ref(
                                event, source_ids_by_task.get(existing_task["task_id"], set())
                            )
                            if "recorded_at" in event and (
                                type(event["recorded_at"]) is not str
                                or not event["recorded_at"].strip()
                            ):
                                raise ValueError("source_event_recorded_at_invalid")
                            _source_event_identity_json(event)
                            replay_identity = dict(event)
                            replay_identity.pop("recorded_at", None)
                            legacy_event_identities[
                                (existing_task["task_id"], event_key)
                            ] = _source_event_identity_json(replay_identity)
                            content = event.get("content")
                            if not _source_event_content_present(content):
                                raise ValueError("source_event_content_required")
                source_events_exist = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source_events'"
                ).fetchone()
                if source_events_exist is not None:
                    for existing_row in conn.execute(
                        "SELECT task_id, event_id, payload_json, recorded_at FROM source_events"
                    ).fetchall():
                        try:
                            event = _decode_source_event_collection(existing_row["payload_json"])
                        except (TypeError, json.JSONDecodeError) as exc:
                            raise ValueError("source_event_payload_invalid") from exc
                        if type(event) is not dict:
                            raise ValueError("source_event_payload_invalid")
                        if not _native_identity(event.get("event_id")):
                            raise ValueError("source_event_identity_invalid")
                        if event["event_id"] != existing_row["event_id"]:
                            raise ValueError("source_event_id_conflict")
                        event_id = event["event_id"]
                        if event_id in source_ids_by_task.get(
                            existing_row["task_id"], set()
                        ):
                            raise ValueError("task_source_identity_invalid")
                        recorded_at = event.get("recorded_at")
                        if not _native_identity(recorded_at):
                            raise ValueError("source_event_recorded_at_invalid")
                        if recorded_at != existing_row["recorded_at"]:
                            raise ValueError("source_event_payload_invalid")
                        _validate_source_event_source_ref(
                            event, source_ids_by_task.get(existing_row["task_id"], set())
                        )
                        _source_event_identity_json(event)
                        replay_identity = dict(event)
                        replay_identity.pop("recorded_at", None)
                        legacy_identity = legacy_event_identities.get(
                            (existing_row["task_id"], event_id)
                        )
                        if (
                            legacy_identity is not None
                            and legacy_identity
                            != _source_event_identity_json(replay_identity)
                        ):
                            raise ValueError("source_event_id_conflict")
                        content = event.get("content")
                        if not _source_event_content_present(content):
                            raise ValueError("source_event_content_required")
            _execute_sql_script_transactionally(
                conn,
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    schema_name TEXT NOT NULL,
                    owner_profile TEXT NOT NULL DEFAULT 'default',
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    original_sources_json TEXT NOT NULL,
                    source_events_json TEXT NOT NULL,
                    spec_bundle_json TEXT NOT NULL,
                    implementation_graph_json TEXT NOT NULL,
                    evidence_graph_json TEXT NOT NULL,
                    closure_json TEXT NOT NULL,
                    spec_revision INTEGER NOT NULL,
                    plan_revision INTEGER NOT NULL,
                    state_revision INTEGER NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS entrypoint_bindings (
                    entrypoint TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (entrypoint, session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_bindings_task ON entrypoint_bindings(task_id);
                CREATE TABLE IF NOT EXISTS source_events (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    event_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_source_events_task ON source_events(task_id, recorded_at, event_id);
                CREATE TRIGGER IF NOT EXISTS source_events_no_update
                BEFORE UPDATE ON source_events
                BEGIN
                    SELECT RAISE(ABORT, 'source_events_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS source_events_no_delete
                BEFORE DELETE ON source_events
                BEGIN
                    SELECT RAISE(ABORT, 'source_events_append_only');
                END;
                CREATE TABLE IF NOT EXISTS spec_reviews (
                    review_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    spec_digest TEXT NOT NULL,
                    source_digest TEXT NOT NULL,
                    reviewer_session_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    findings_json TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    UNIQUE (task_id, spec_digest, source_digest, reviewer_session_id)
                );
                CREATE INDEX IF NOT EXISTS idx_spec_reviews_task
                    ON spec_reviews(task_id, recorded_at, review_id);
                CREATE TRIGGER IF NOT EXISTS spec_reviews_no_update
                BEFORE UPDATE ON spec_reviews
                BEGIN
                    SELECT RAISE(ABORT, 'spec_reviews_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS spec_reviews_no_delete
                BEFORE DELETE ON spec_reviews
                BEGIN
                    SELECT RAISE(ABORT, 'spec_reviews_append_only');
                END;
                CREATE TABLE IF NOT EXISTS task_state_events (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    state_revision INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    previous_event_digest TEXT NOT NULL,
                    event_digest TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    PRIMARY KEY (task_id, state_revision),
                    UNIQUE (task_id, event_digest)
                );
                CREATE INDEX IF NOT EXISTS idx_task_state_events_task
                    ON task_state_events(task_id, state_revision);
                CREATE TRIGGER IF NOT EXISTS task_state_events_no_update
                BEFORE UPDATE ON task_state_events
                BEGIN
                    SELECT RAISE(ABORT, 'task_state_events_append_only');
                END;
                CREATE TRIGGER IF NOT EXISTS task_state_events_no_delete
                BEFORE DELETE ON task_state_events
                BEGIN
                    SELECT RAISE(ABORT, 'task_state_events_append_only');
                END;
                CREATE TABLE IF NOT EXISTS task_slices (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    definition_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    lease_expires_at REAL,
                    result_id TEXT NOT NULL,
                    integrated_state_revision INTEGER,
                    PRIMARY KEY (task_id, slice_id)
                );
                CREATE TABLE IF NOT EXISTS context_packets (
                    packet_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    packet_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    spec_revision INTEGER NOT NULL,
                    plan_revision INTEGER NOT NULL,
                    state_revision INTEGER NOT NULL,
                    lease_owner TEXT NOT NULL,
                    lease_token TEXT NOT NULL,
                    lease_expires_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_packets_task_slice ON context_packets(task_id, slice_id);
                CREATE TABLE IF NOT EXISTS worker_results (
                    result_id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL REFERENCES tasks(task_id) ON DELETE CASCADE,
                    slice_id TEXT NOT NULL,
                    packet_id TEXT NOT NULL REFERENCES context_packets(packet_id),
                    worker_session_id TEXT NOT NULL,
                    payload_digest TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    disposition TEXT NOT NULL,
                    reasons_json TEXT NOT NULL,
                    integrator_session_id TEXT NOT NULL,
                    integrated_state_revision INTEGER,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_results_task ON worker_results(task_id, disposition);
                """
            )
            task_columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "owner_profile" not in task_columns:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN owner_profile TEXT NOT NULL DEFAULT 'default'"
                )
                conn.execute(
                    "UPDATE tasks SET owner_profile = ?",
                    (self.owner_profile,),
                )
            # Original SourceArtifacts remain authoritative across upgrades. Revalidate
            # persisted native identities before accepting old task state.
            for existing_task in conn.execute(
                "SELECT original_sources_json FROM tasks"
            ).fetchall():
                try:
                    existing_sources = _decode_source_event_collection(
                        existing_task["original_sources_json"]
                    )
                    _validate_original_sources(existing_sources, allow_empty=True)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("original_source_payload_invalid") from exc
            # Rows normalized by older runtimes remain authoritative on upgrade, so
            # revalidate them before accepting or comparing any legacy projection.
            existing_source_events = conn.execute(
                "SELECT task_id, event_id, payload_json, recorded_at FROM source_events"
            ).fetchall()
            for existing_row in existing_source_events:
                try:
                    existing_payload = _decode_source_event_collection(
                        existing_row["payload_json"]
                    )
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("source_event_payload_invalid") from exc
                if type(existing_payload) is not dict:
                    raise ValueError("source_event_payload_invalid")
                existing_event_id = existing_payload.get("event_id")
                if type(existing_event_id) is not str or not existing_event_id.strip():
                    raise ValueError("source_event_identity_invalid")
                if existing_event_id != existing_row["event_id"]:
                    raise ValueError("source_event_id_conflict")
                recorded_at = existing_payload.get("recorded_at")
                if not _native_identity(recorded_at):
                    raise ValueError("source_event_recorded_at_invalid")
                if recorded_at != existing_row["recorded_at"]:
                    raise ValueError("source_event_payload_invalid")
                existing_identity = dict(existing_payload)
                existing_identity.pop("recorded_at", None)
                _source_event_identity_json(existing_identity)
                existing_content = existing_identity.get("content")
                if not _source_event_content_present(existing_content):
                    raise ValueError("source_event_content_required")
            # One-time compatibility backfill from the legacy mutable JSON snapshot.
            # The normalized table is authoritative after this point; exact semantic
            # replay is accepted, while conflicting reuse of an event identity fails closed.
            rows = conn.execute(
                "SELECT task_id, source_events_json, created_at FROM tasks "
                "WHERE source_events_json != '[]'"
            ).fetchall()
            for row in rows:
                raw_source_events = row["source_events_json"]
                try:
                    source_events = _decode_source_event_collection(raw_source_events)
                except (TypeError, json.JSONDecodeError) as exc:
                    raise ValueError("source_event_collection_invalid") from exc
                if not isinstance(source_events, list):
                    raise ValueError("source_event_collection_invalid")
                normalized_events: list[dict[str, Any]] = []
                event_identities: dict[str, str] = {}
                for ordinal, event in enumerate(source_events):
                    if not isinstance(event, dict):
                        raise ValueError("source_event_invalid")
                    raw_event_id = event.get("event_id")
                    if type(raw_event_id) is not str or not raw_event_id.strip():
                        raise ValueError("source_event_identity_invalid")
                    event_id = raw_event_id
                    payload = dict(event)
                    payload["event_id"] = event_id
                    if "recorded_at" in payload:
                        recorded_at = payload["recorded_at"]
                        if type(recorded_at) is not str or not recorded_at.strip():
                            raise ValueError("source_event_recorded_at_invalid")
                    else:
                        recorded_at = _legacy_event_recorded_at(row["created_at"], ordinal)
                    payload["recorded_at"] = recorded_at
                    identity = dict(payload)
                    identity.pop("recorded_at", None)
                    identity_json = _source_event_identity_json(identity)
                    content = identity.get("content")
                    if not _source_event_content_present(content):
                        raise ValueError("source_event_content_required")
                    existing_identity = event_identities.get(event_id)
                    if existing_identity is not None:
                        if existing_identity != identity_json:
                            raise ValueError("source_event_id_conflict")
                        continue
                    event_identities[event_id] = identity_json
                    normalized_events.append(payload)
                for payload in normalized_events:
                    event_id = payload["event_id"]
                    existing_row = conn.execute(
                        "SELECT payload_json FROM source_events "
                        "WHERE task_id = ? AND event_id = ?",
                        (row["task_id"], event_id),
                    ).fetchone()
                    if existing_row is not None:
                        existing_identity = _decode(existing_row["payload_json"], {})
                        existing_identity.pop("recorded_at", None)
                        if (
                            _source_event_identity_json(existing_identity)
                            != event_identities[event_id]
                        ):
                            raise ValueError("source_event_id_conflict")
                        continue
                    conn.execute(
                        "INSERT INTO source_events "
                        "(task_id, event_id, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
                        (row["task_id"], event_id, _json(payload), payload["recorded_at"]),
                    )
            legacy_rows = conn.execute(
                "SELECT task_id, state_revision, status FROM tasks WHERE NOT EXISTS "
                "(SELECT 1 FROM task_state_events e WHERE e.task_id = tasks.task_id)"
            ).fetchall()
            for row in legacy_rows:
                self._record_state_event(
                    conn,
                    row["task_id"],
                    int(row["state_revision"]),
                    "legacy_projection_anchored",
                    {"status": row["status"]},
                )
            # Existing state-event ledgers predate projection binding. Append one auditable
            # anchor rather than rewriting historical event bytes.
            unbound_rows = conn.execute(
                "SELECT t.task_id, e.payload_json FROM tasks t "
                "JOIN task_state_events e ON e.task_id = t.task_id "
                "WHERE e.state_revision = t.state_revision"
            ).fetchall()
            for row in unbound_rows:
                payload = _decode(row["payload_json"], {})
                if not str(payload.get("_projection_digest") or ""):
                    self._bump_state(
                        conn,
                        row["task_id"],
                        "projection_integrity_anchored",
                        {"previous_tip_unbound": True},
                    )
        self._ensure_wal_mode()

    @staticmethod
    def default_path() -> Path:
        import os

        explicit = os.environ.get("HERMES_TASK_RUNTIME_DB")
        if explicit:
            return Path(explicit).expanduser()

        from hermes_constants import get_hermes_home

        return get_hermes_home() / "state" / "task-runtime" / "task-runtime.db"

    def create_task(
        self,
        *,
        title: str,
        original_sources: Sequence[Mapping[str, Any]],
        task_id: str | None = None,
    ) -> dict[str, Any]:
        sources = [dict(item) for item in original_sources]
        _validate_original_sources(sources)
        now = _utc_now()
        identity = task_id or _task_id()
        with self._write() as conn:
            conn.execute(
                """
                INSERT INTO tasks (
                    task_id, schema_name, owner_profile, title, status, original_sources_json,
                    source_events_json, spec_bundle_json, implementation_graph_json,
                    evidence_graph_json, closure_json, spec_revision, plan_revision,
                    state_revision, candidate_digest, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'draft', ?, '[]', '{}', '[]', '[]', '{}', 0, 0, 1, '', ?, ?)
                """,
                (
                    identity,
                    SCHEMA,
                    self.owner_profile,
                    str(title or "Untitled task"),
                    _json(sources),
                    now,
                    now,
                ),
            )
            self._record_state_event(
                conn,
                identity,
                1,
                "task_created",
                {
                    "title": str(title or "Untitled task"),
                    "owner_profile": self.owner_profile,
                },
            )
        return self.get_task(identity)

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(task_id)
            self._assert_owner(row)
            slices = conn.execute(
                "SELECT slice_id, ordinal, definition_json, status, lease_owner, "
                "lease_expires_at, result_id, integrated_state_revision "
                "FROM task_slices WHERE task_id = ? ORDER BY ordinal, slice_id",
                (task_id,),
            ).fetchall()
            bindings = conn.execute(
                "SELECT entrypoint, session_id, created_at FROM entrypoint_bindings "
                "WHERE task_id = ? ORDER BY entrypoint, session_id",
                (task_id,),
            ).fetchall()
            source_events = conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? "
                "ORDER BY rowid",
                (task_id,),
            ).fetchall()
            state_events = conn.execute(
                "SELECT state_revision, event_type, payload_json, previous_event_digest, "
                "event_digest, recorded_at FROM task_state_events WHERE task_id = ? "
                "ORDER BY state_revision",
                (task_id,),
            ).fetchall()
            spec_reviews = conn.execute(
                "SELECT review_id, spec_digest, source_digest, reviewer_session_id, "
                "verdict, findings_json, recorded_at FROM spec_reviews WHERE task_id = ? "
                "ORDER BY recorded_at, review_id",
                (task_id,),
            ).fetchall()
            projection_digest = self._projection_digest(conn, task_id)
        data = dict(row)
        if not self._state_event_ledger_valid(
            task_id, int(data["state_revision"]), state_events
        ):
            raise ValueError("task_state_event_ledger_invalid")
        tip_payload = _decode(state_events[-1]["payload_json"], {})
        if str(tip_payload.get("_projection_digest") or "") != projection_digest:
            raise ValueError("task_projection_digest_invalid")
        return {
            "schema": data["schema_name"],
            "task_id": data["task_id"],
            "owner_profile": data["owner_profile"],
            "title": data["title"],
            "status": data["status"],
            "original_sources": _decode(data["original_sources_json"], []),
            "source_events": [_decode(item["payload_json"], {}) for item in source_events],
            "spec_reviews": [
                {
                    "review_id": item["review_id"],
                    "spec_digest": item["spec_digest"],
                    "source_digest": item["source_digest"],
                    "reviewer_session_id": item["reviewer_session_id"],
                    "verdict": item["verdict"],
                    "findings": _decode(item["findings_json"], []),
                    "recorded_at": item["recorded_at"],
                }
                for item in spec_reviews
            ],
            "state_events": [
                {
                    "state_revision": int(item["state_revision"]),
                    "event_type": item["event_type"],
                    "payload": _decode(item["payload_json"], {}),
                    "previous_event_digest": item["previous_event_digest"],
                    "event_digest": item["event_digest"],
                    "recorded_at": item["recorded_at"],
                }
                for item in state_events
            ],
            "spec_bundle": _decode(data["spec_bundle_json"], {}),
            "implementation_graph": _decode(data["implementation_graph_json"], []),
            "evidence_graph": _decode(data["evidence_graph_json"], []),
            "closure": _decode(data["closure_json"], {}),
            "spec_revision": int(data["spec_revision"]),
            "plan_revision": int(data["plan_revision"]),
            "state_revision": int(data["state_revision"]),
            "candidate_digest": data["candidate_digest"],
            "created_at": data["created_at"],
            "updated_at": data["updated_at"],
            "bindings": [dict(item) for item in bindings],
            "slices": [
                {
                    **_decode(item["definition_json"], {}),
                    "status": item["status"],
                    "lease_owner": item["lease_owner"],
                    "lease_expires_at": item["lease_expires_at"],
                    "result_id": item["result_id"],
                    "integrated_state_revision": item["integrated_state_revision"],
                }
                for item in slices
            ],
        }

    def bind_entrypoint(self, task_id: str, *, entrypoint: str, session_id: str) -> dict[str, Any]:
        entrypoint = str(entrypoint or "").strip().lower()
        session_id = str(session_id or "").strip()
        if not entrypoint or not session_id:
            raise ValueError("entrypoint_and_session_id_required")
        with self._write() as conn:
            self._task_row(conn, task_id)
            existing = conn.execute(
                "SELECT task_id FROM entrypoint_bindings WHERE entrypoint = ? AND session_id = ?",
                (entrypoint, session_id),
            ).fetchone()
            if existing is not None:
                if existing["task_id"] != task_id:
                    raise ValueError("entrypoint_already_bound")
            else:
                conn.execute(
                    """
                    INSERT INTO entrypoint_bindings (entrypoint, session_id, task_id, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (entrypoint, session_id, task_id, _utc_now()),
                )
                self._bump_state(
                    conn,
                    task_id,
                    "entrypoint_bound",
                    {"entrypoint": entrypoint, "session_id": session_id},
                )
        return self.get_task(task_id)

    def resolve_entrypoint(self, entrypoint: str, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_id FROM entrypoint_bindings WHERE entrypoint = ? AND session_id = ?",
                (str(entrypoint or "").strip().lower(), str(session_id or "").strip()),
            ).fetchone()
        return self.get_task(row["task_id"]) if row else None

    @staticmethod
    def _source_snapshot(conn: sqlite3.Connection, task: sqlite3.Row) -> dict[str, Any]:
        events = [
            _decode(row["payload_json"], {})
            for row in conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? "
                "ORDER BY rowid",
                (task["task_id"],),
            ).fetchall()
        ]
        return {
            "original_sources": _decode(task["original_sources_json"], []),
            "source_events": events,
        }

    @staticmethod
    def _source_ids(source_snapshot: Mapping[str, Any]) -> list[str]:
        identifiers = [
            item.get("source_id")
            for item in source_snapshot.get("original_sources") or []
            if isinstance(item, Mapping)
        ]
        identifiers.extend(
            item.get("event_id")
            for item in source_snapshot.get("source_events") or []
            if isinstance(item, Mapping)
        )
        native_identifiers: list[str] = []
        for item in identifiers:
            if type(item) is not str or not item.strip():
                raise ValueError("task_source_identity_invalid")
            native_identifiers.append(item)
        if len(native_identifiers) != len(set(native_identifiers)):
            raise ValueError("task_source_identity_invalid")
        return native_identifiers

    @staticmethod
    def _validate_governed_spec(
        spec: Mapping[str, Any], *, source_ids: Sequence[str],
        source_snapshot: Mapping[str, Any] | None = None,
    ) -> None:
        governance = spec.get("governance")
        if not isinstance(governance, Mapping):
            raise ValueError("spec_governance_required")
        task_class = governance.get("task_class")
        if type(task_class) is not str or task_class not in TASK_CLASSES:
            raise ValueError("spec_task_class_invalid")

        known_sources = set(source_ids)
        source_records = [
            item
            for item in reversed((source_snapshot or {}).get("source_events") or [])
            if isinstance(item, Mapping)
        ]
        source_records.extend(
            item
            for item in (source_snapshot or {}).get("original_sources") or []
            if isinstance(item, Mapping)
        )
        non_authoritative_kinds = STRICT_NON_AUTHORITATIVE_SOURCE_KINDS
        authoritative_ids = [
            _source_record_identity(item)
            for item in source_records
            if str(item.get("source_kind") or item.get("kind") or "").strip().lower()
            not in non_authoritative_kinds
        ]
        if not source_records:
            authoritative_ids = list(source_ids)
        source_authority_raw = governance.get("source_authority")
        if not isinstance(source_authority_raw, list) or any(
            type(item) is not str or not item.strip() for item in source_authority_raw
        ):
            raise ValueError("spec_source_authority_invalid")
        source_authority = list(source_authority_raw)
        oracle_refs_raw = governance.get("oracle_refs")
        if not isinstance(oracle_refs_raw, list) or any(
            type(item) is not str or not item.strip() for item in oracle_refs_raw
        ):
            raise ValueError("spec_oracle_refs_invalid")
        oracle_refs = set(oracle_refs_raw)
        if not oracle_refs or not oracle_refs.issubset(known_sources):
            raise ValueError("spec_oracle_refs_invalid")
        if not oracle_refs.issubset(set(authoritative_ids)):
            raise ValueError("spec_oracle_refs_not_authoritative")
        if (
            not source_authority
            or len(source_authority) != len(set(source_authority))
            or set(source_authority) != set(authoritative_ids)
        ):
            raise ValueError("spec_source_authority_incomplete")
        source_events = (source_snapshot or {}).get("source_events") or []
        if not source_events and source_authority != authoritative_ids:
            raise ValueError("spec_source_authority_order_invalid")

        target_profile = governance.get("target_profile")
        if target_profile is not None and type(target_profile) is not str:
            raise ValueError("spec_target_profile_invalid")
        if target_profile == "strict_ui":
            ranked_sources: list[tuple[int, str]] = []
            immutable_locators: dict[str, str] = {}
            for item in source_records:
                source_id = _source_record_identity(item)
                source_kind = item.get("source_kind")
                if type(source_kind) is not str or not source_kind.strip():
                    raise ValueError("source_artifact_kind_required")
                source_kind = source_kind.strip()
                if source_kind not in STRICT_SOURCE_KINDS:
                    raise ValueError("source_artifact_kind_invalid")
                content = item.get("content")
                content_digest = item.get("content_digest")
                if type(content_digest) is not str or type(content) is not str or content_digest != (
                    "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
                ):
                    raise ValueError("source_artifact_digest_mismatch")
                locator = item.get("immutable_locator")
                if type(locator) is not str:
                    raise ValueError("source_artifact_locator_not_immutable")
                digest_addressed = locator.startswith(("inline:", "cas:", "file:")) and (
                    content_digest in locator
                )
                session_addressed = (
                    locator.startswith("session://")
                    and "?" not in locator
                    and "#" not in locator
                    and len(
                        [
                            part
                            for part in locator.removeprefix("session://").split("/")
                            if part
                        ]
                    ) >= 3
                )
                if not (digest_addressed or session_addressed):
                    raise ValueError("source_artifact_locator_not_immutable")
                immutable_locators[source_id] = locator
                rank = item.get("authority_rank")
                if isinstance(rank, bool) or not isinstance(rank, int) or rank < 0:
                    raise ValueError("source_artifact_authority_rank_invalid")
                derived_from = item.get("derived_from")
                if not isinstance(derived_from, list) or any(
                    type(ref) is not str or ref not in known_sources
                    for ref in derived_from
                ):
                    raise ValueError("source_artifact_derived_from_invalid")
                if source_kind in STRICT_NON_AUTHORITATIVE_SOURCE_KINDS and not derived_from:
                    raise ValueError("source_artifact_derived_from_required")
                if source_kind in STRICT_AUTHORITATIVE_SOURCE_KINDS:
                    ranked_sources.append((rank, source_id))
            if len({rank for rank, _ in ranked_sources}) != len(ranked_sources):
                raise ValueError("source_artifact_authority_rank_conflict")
            ranked_authority = [source_id for _, source_id in sorted(ranked_sources)]
            if source_authority != ranked_authority:
                if set(source_authority) == set(ranked_authority):
                    raise ValueError("spec_source_authority_order_invalid")
                raise ValueError("spec_source_authority_incomplete")

            obligation_manifest = governance.get("source_obligations")
            if not isinstance(obligation_manifest, Mapping) or set(
                obligation_manifest
            ) != STRICT_UI_OBLIGATION_FIELDS:
                raise ValueError("source_obligation_manifest_incomplete")
            obligations: set[str] = set()
            for field in STRICT_UI_OBLIGATION_FIELDS:
                values = obligation_manifest.get(field)
                if not isinstance(values, list) or not values or any(
                    type(value) is not str or not value.strip() for value in values
                ):
                    raise ValueError("source_obligation_manifest_incomplete")
                obligations.update(f"{field}:{value}" for value in values)
            strict_claims = [
                claim
                for field in ("required_clauses", "negative_requirements")
                for claim in (spec.get(field) or [])
                if isinstance(claim, Mapping)
            ]
            admitted_obligations: set[str] = set()
            for claim in strict_claims:
                claim_obligations = claim.get("obligation_refs") or []
                if (
                    not isinstance(claim_obligations, list)
                    or not claim_obligations
                    or any(type(ref) is not str or not ref for ref in claim_obligations)
                ):
                    raise ValueError("source_obligation_scope_incomplete")
                admitted_obligations.update(claim_obligations)
                if claim.get("scope_quantifier") != "all":
                    raise ValueError("source_quantifier_weakened")
                anchors = claim.get("source_anchors")
                if not isinstance(anchors, list) or not anchors:
                    raise ValueError("source_anchor_required")
                for anchor in anchors:
                    anchor_source = anchor.get("source_ref") if isinstance(anchor, Mapping) else None
                    anchor_locator = anchor.get("locator") if isinstance(anchor, Mapping) else None
                    if (
                        not isinstance(anchor, Mapping)
                        or type(anchor_source) is not str
                        or type(anchor_locator) is not str
                        or anchor_source not in known_sources
                        or not anchor_locator
                    ):
                        raise ValueError("source_anchor_invalid")
                    if anchor_source not in source_authority:
                        raise ValueError("source_anchor_not_authoritative")
                    immutable_locator = immutable_locators.get(anchor_source, "")
                    if not immutable_locator or (
                        anchor_locator != immutable_locator
                        and not anchor_locator.startswith(immutable_locator + "#")
                    ):
                        raise ValueError("source_anchor_not_immutable")
                claim_oracles = claim.get("oracle_refs")
                if (
                    not isinstance(claim_oracles, list)
                    or not claim_oracles
                    or any(type(ref) is not str or not ref for ref in claim_oracles)
                    or not set(claim_oracles).issubset(oracle_refs)
                ):
                    raise ValueError("source_oracle_binding_required")
                claim_dimensions = claim.get("fidelity_dimensions")
                if (
                    not isinstance(claim_dimensions, list)
                    or not claim_dimensions
                    or any(type(ref) is not str or not ref for ref in claim_dimensions)
                    or not set(claim_dimensions).issubset(
                        set(obligation_manifest["fidelity_dimensions"])
                    )
                ):
                    raise ValueError("source_fidelity_dimensions_required")
            if admitted_obligations != obligations:
                raise ValueError("source_obligation_scope_incomplete")
        source_coverage = governance.get("source_coverage")
        if not isinstance(source_coverage, Mapping) or set(source_coverage) != known_sources:
            raise ValueError("spec_source_coverage_incomplete")
        if any(
            not isinstance(source_coverage[source_id], list)
            or not source_coverage[source_id]
            or any(
                type(item) is not str or not item.strip()
                for item in source_coverage[source_id]
            )
            for source_id in known_sources
        ):
            raise ValueError("spec_source_coverage_incomplete")

        claim_ids: set[str] = set()
        claim_source_edges: set[tuple[str, str]] = set()
        for field in ("required_clauses", "negative_requirements"):
            claims = spec.get(field)
            if not isinstance(claims, list) or not claims:
                raise ValueError(f"spec_{field}_required")
            for claim in claims:
                if not isinstance(claim, Mapping):
                    raise ValueError("spec_claim_invalid")
                claim_id_raw = claim.get("id")
                if type(claim_id_raw) is not str or not claim_id_raw.strip():
                    raise ValueError("spec_claim_identity_invalid")
                claim_id = claim_id_raw
                claim_body = claim.get("text")
                if not isinstance(claim_body, str) or not claim_body.strip():
                    raise ValueError("spec_claim_body_required")
                claim_refs_raw = claim.get("source_refs")
                if not isinstance(claim_refs_raw, list) or any(
                    type(item) is not str or not item.strip() for item in claim_refs_raw
                ):
                    raise ValueError("spec_claim_source_refs_invalid")
                refs = set(claim_refs_raw)
                if not claim_id or claim_id in claim_ids:
                    raise ValueError("spec_claim_identity_invalid")
                if not refs or not refs.issubset(known_sources):
                    raise ValueError("spec_claim_source_refs_invalid")
                claim_ids.add(claim_id)
                claim_source_edges.update((source_id, claim_id) for source_id in refs)
        covered_claim_ids = {
            claim_id
            for source_id in known_sources
            for claim_id in source_coverage[source_id]
        }
        if covered_claim_ids != claim_ids:
            raise ValueError("spec_source_coverage_incomplete")
        coverage_edges = {
            (source_id, claim_id)
            for source_id in known_sources
            for claim_id in source_coverage[source_id]
        }
        if coverage_edges != claim_source_edges:
            raise ValueError("spec_source_coverage_edge_mismatch")
        closure_criteria = spec.get("closure_criteria")
        if (
            not isinstance(closure_criteria, list)
            or any(type(item) is not str or not item.strip() for item in closure_criteria)
            or set(closure_criteria) != claim_ids
        ):
            raise ValueError("spec_closure_coverage_incomplete")

        discovery = governance.get("discovery")
        if not isinstance(discovery, Mapping) or not DISCOVERY_LANES.issubset(discovery):
            raise ValueError("spec_discovery_preflight_incomplete")
        for lane_name in DISCOVERY_LANES:
            lane = discovery[lane_name]
            if not isinstance(lane, Mapping):
                raise ValueError("spec_discovery_preflight_invalid")
            status = lane.get("status")
            lane_refs_raw = lane.get("source_refs")
            if not isinstance(lane_refs_raw, list) or any(
                type(item) is not str or not item.strip() for item in lane_refs_raw
            ):
                raise ValueError("spec_discovery_preflight_invalid")
            refs = set(lane_refs_raw)
            if status not in PREFLIGHT_STATUSES:
                raise ValueError("spec_discovery_preflight_invalid")
            if not refs or not refs.issubset(known_sources):
                raise ValueError("spec_discovery_source_refs_invalid")
            decision = lane.get("decision")
            if type(decision) is not str or not decision.strip():
                raise ValueError("spec_discovery_decision_required")
        if discovery["project_evidence"].get("status") != "completed":
            raise ValueError("project_evidence_preflight_required")
        required_lane = {
            "mature_capability": "open_source",
            "product_design": "competitor",
            "reference_reimplementation": "prototype",
        }.get(task_class)
        if required_lane and discovery[required_lane].get("status") != "completed":
            raise ValueError(f"{required_lane}_preflight_required_for_task_class")
        if task_class == "reference_reimplementation":
            prototype_refs = set(discovery["prototype"].get("source_refs") or [])
            if not prototype_refs or not prototype_refs.issubset(oracle_refs):
                raise ValueError("prototype_oracle_binding_required")
        if discovery["open_source"].get("status") == "completed":
            evaluation = discovery["open_source"].get("evaluation")
            if (
                not isinstance(evaluation, Mapping)
                or not OPEN_SOURCE_EVALUATION_DIMENSIONS.issubset(evaluation)
                or any(
                    type(evaluation[dimension]) is not str
                    or not evaluation[dimension].strip()
                    for dimension in OPEN_SOURCE_EVALUATION_DIMENSIONS
                )
            ):
                raise ValueError("open_source_evaluation_incomplete")
            if not _is_exact_version_pin(evaluation["version_pin"]):
                raise ValueError("open_source_version_pin_invalid")
        if governance.get("blocking_unknowns") != []:
            raise ValueError("spec_blocking_unknowns_unresolved")

    def record_spec_review(
        self,
        task_id: str,
        spec_bundle: Mapping[str, Any],
        *,
        reviewer_session_id: str,
        verdict: str,
        findings: Sequence[Mapping[str, Any] | str],
        expected_state_revision: int,
    ) -> dict[str, Any]:
        reviewer = str(reviewer_session_id or "").strip()
        normalized_verdict = str(verdict or "").strip().lower()
        normalized_findings = list(findings)
        if not reviewer:
            raise ValueError("spec_reviewer_session_id_required")
        if normalized_verdict not in {"approved", "rejected"}:
            raise ValueError("spec_review_verdict_invalid")
        if normalized_verdict == "rejected" and not normalized_findings:
            raise ValueError("rejected_spec_review_findings_required")
        spec = dict(spec_bundle)
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            source_snapshot = self._source_snapshot(conn, task)
            source_ids = self._source_ids(source_snapshot)
            if normalized_verdict == "approved":
                self._validate_governed_spec(
                    spec, source_ids=source_ids, source_snapshot=source_snapshot
                )
            spec_digest = _digest(spec)
            source_digest = _digest(source_snapshot)
            review_id = _digest(
                {
                    "task_id": task_id,
                    "spec_digest": spec_digest,
                    "source_digest": source_digest,
                    "reviewer_session_id": reviewer,
                }
            )
            existing = conn.execute(
                "SELECT verdict, findings_json FROM spec_reviews WHERE review_id = ?",
                (review_id,),
            ).fetchone()
            if existing is not None:
                if (
                    str(existing["verdict"]) != normalized_verdict
                    or _decode(existing["findings_json"], []) != normalized_findings
                ):
                    raise ValueError("spec_review_identity_conflict")
                current = self._task_row(conn, task_id)
                state_revision = int(current["state_revision"])
            else:
                self._expect_revision(task, expected_state_revision)
                conn.execute(
                    "INSERT INTO spec_reviews (review_id, task_id, spec_digest, source_digest, "
                    "reviewer_session_id, verdict, findings_json, recorded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        review_id,
                        task_id,
                        spec_digest,
                        source_digest,
                        reviewer,
                        normalized_verdict,
                        _json(normalized_findings),
                        _utc_now(),
                    ),
                )
                self._bump_state(
                    conn,
                    task_id,
                    "spec_review_recorded",
                    {
                        "review_id": review_id,
                        "spec_digest": spec_digest,
                        "source_digest": source_digest,
                        "reviewer_session_id": reviewer,
                        "verdict": normalized_verdict,
                    },
                )
                state_revision = int(task["state_revision"]) + 1
        return {
            "review_id": review_id,
            "task_id": task_id,
            "spec_digest": spec_digest,
            "source_digest": source_digest,
            "reviewer_session_id": reviewer,
            "verdict": normalized_verdict,
            "findings": normalized_findings,
            "state_revision": state_revision,
        }

    def append_source_event(self, task_id: str, event: Mapping[str, Any]) -> dict[str, Any]:
        payload = dict(event)
        if not _source_event_content_present(payload.get("content")):
            raise ValueError("source_event_content_required")
        event_id_supplied = "event_id" in payload
        raw_event_id = payload.get("event_id")
        if event_id_supplied and not _native_identity(raw_event_id):
            raise ValueError("source_event_identity_invalid")
        if "source_id" in payload and not _native_identity(payload.get("source_id")):
            raise ValueError("source_event_source_identity_invalid")
        if "recorded_at" in payload and not _native_identity(payload.get("recorded_at")):
            raise ValueError("source_event_recorded_at_invalid")
        identity_payload = dict(payload)
        identity_payload.pop("recorded_at", None)
        event_id = raw_event_id if event_id_supplied else _digest(identity_payload)
        replay_identity = dict(payload)
        replay_identity["event_id"] = event_id
        replay_identity.pop("recorded_at", None)
        identity_json = _source_event_identity_json(replay_identity)
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            original_sources = _validate_original_sources(
                _decode_source_event_collection(task["original_sources_json"]),
                allow_empty=True,
            )
            source_artifact_ids = {
                _source_record_identity(item) for item in original_sources
            }
            _validate_source_event_source_ref(payload, source_artifact_ids)
            if event_id in source_artifact_ids:
                raise ValueError("task_source_identity_invalid")
            existing_row = conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? AND event_id = ?",
                (task_id, event_id),
            ).fetchone()
            if existing_row is not None:
                existing_identity = _decode(existing_row["payload_json"], {})
                existing_identity.pop("recorded_at", None)
                if _source_event_identity_json(existing_identity) != identity_json:
                    raise ValueError("source_event_id_conflict")
            else:
                payload["event_id"] = event_id
                payload["recorded_at"] = _utc_now()
                conn.execute(
                    "INSERT INTO source_events (task_id, event_id, payload_json, recorded_at) "
                    "VALUES (?, ?, ?, ?)",
                    (task_id, event_id, _json(payload), str(payload["recorded_at"])),
                )
                spec_invalidated = str(task["status"]) != "draft"
                closure_invalidated = str(task["status"]) == "closed"
                if spec_invalidated:
                    self._invalidate_claims(conn, task_id, "source_event_changed")
                    conn.execute(
                        "UPDATE tasks SET status = 'draft', candidate_digest = '', "
                        "plan_revision = plan_revision + CASE "
                        "WHEN implementation_graph_json != '[]' THEN 1 ELSE 0 END, "
                        "implementation_graph_json = '[]', evidence_graph_json = '[]', "
                        "closure_json = '{}' WHERE task_id = ?",
                        (task_id,),
                    )
                    conn.execute("DELETE FROM task_slices WHERE task_id = ?", (task_id,))
                self._bump_state(
                    conn,
                    task_id,
                    "source_event_appended",
                    {
                        "event_id": event_id,
                        "spec_invalidated": spec_invalidated,
                        "closure_invalidated": closure_invalidated,
                    },
                )
        return self.get_task(task_id)

    def freeze_spec(
        self,
        task_id: str,
        spec_bundle: Mapping[str, Any],
        *,
        compiler_session_id: str | None = None,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        spec = dict(spec_bundle)
        compiler = str(compiler_session_id or "").strip()
        if not compiler:
            raise ValueError("spec_compiler_session_id_required")
        if not spec.get("required_clauses") or not spec.get("closure_criteria"):
            raise ValueError("spec_missing_required_clauses_or_closure_criteria")
        with self._write() as conn:
            row = self._task_row(conn, task_id)
            self._expect_revision(row, expected_state_revision)
            spec_digest = _digest(spec)
            source_snapshot = self._source_snapshot(conn, row)
            source_digest = _digest(source_snapshot)
            accepted_review: sqlite3.Row | None = None
            source_ids = self._source_ids(source_snapshot)
            self._validate_governed_spec(
                spec, source_ids=source_ids, source_snapshot=source_snapshot
            )
            approved_reviews = conn.execute(
                "SELECT reviewer_session_id, review_id FROM spec_reviews "
                "WHERE task_id = ? AND spec_digest = ? AND source_digest = ? "
                "AND verdict = 'approved' ORDER BY recorded_at, review_id",
                (task_id, spec_digest, source_digest),
            ).fetchall()
            if not approved_reviews:
                raise ValueError("independent_spec_review_required")
            independent_reviews = [
                review
                for review in approved_reviews
                if str(review["reviewer_session_id"]) != compiler
            ]
            if not independent_reviews:
                raise ValueError("spec_compiler_and_reviewer_must_be_independent")
            accepted_review = independent_reviews[-1]
            conn.execute(
                """
                UPDATE tasks SET spec_bundle_json = ?, spec_revision = spec_revision + 1,
                    plan_revision = plan_revision + CASE
                        WHEN implementation_graph_json != '[]' THEN 1 ELSE 0 END,
                    implementation_graph_json = '[]',
                    state_revision = state_revision + 1, status = 'specified',
                    candidate_digest = '', evidence_graph_json = '[]', closure_json = '{}',
                    updated_at = ?
                WHERE task_id = ?
                """,
                (_json(spec), _utc_now(), task_id),
            )
            self._invalidate_claims(conn, task_id, "spec_changed")
            conn.execute("DELETE FROM task_slices WHERE task_id = ?", (task_id,))
            event_payload: dict[str, Any] = {
                "spec_revision": int(row["spec_revision"]) + 1,
                "spec_digest": spec_digest,
                "source_digest": source_digest,
            }
            if accepted_review is not None:
                event_payload.update(
                    {
                        "compiler_session_id": compiler,
                        "review_id": str(accepted_review["review_id"]),
                        "reviewer_session_id": str(accepted_review["reviewer_session_id"]),
                    }
                )
            self._record_state_event(
                conn,
                task_id,
                int(row["state_revision"]) + 1,
                "spec_frozen",
                event_payload,
            )
        return self.get_task(task_id)

    def set_plan(
        self,
        task_id: str,
        slices: Sequence[Mapping[str, Any]],
        *,
        expected_state_revision: int,
    ) -> dict[str, Any]:
        definitions = [dict(item) for item in slices]
        self._validate_plan(definitions)
        with self._write() as conn:
            row = self._task_row(conn, task_id)
            self._expect_revision(row, expected_state_revision)
            if int(row["spec_revision"]) < 1:
                raise ValueError("spec_not_frozen")
            spec = _decode(row["spec_bundle_json"], {})
            if str(row["status"]) not in {"specified", "planned", "running"}:
                raise ValueError("task_not_specified_for_plan")
            source_snapshot = self._source_snapshot(conn, row)
            source_ids = self._source_ids(source_snapshot)
            self._validate_governed_spec(
                spec, source_ids=source_ids, source_snapshot=source_snapshot
            )
            approved_review = conn.execute(
                "SELECT 1 FROM spec_reviews WHERE task_id = ? AND spec_digest = ? "
                "AND source_digest = ? AND verdict = 'approved' LIMIT 1",
                (task_id, _digest(spec), _digest(source_snapshot)),
            ).fetchone()
            if approved_review is None:
                raise ValueError("independent_spec_review_required")
            current_spec_revision = int(row["spec_revision"])
            current_spec_digest = _digest(spec)
            current_source_digest = _digest(source_snapshot)
            frozen_events = conn.execute(
                "SELECT payload_json FROM task_state_events "
                "WHERE task_id = ? AND event_type = 'spec_frozen'",
                (task_id,),
            ).fetchall()
            current_spec_frozen = any(
                int(payload.get("spec_revision") or -1) == current_spec_revision
                and str(payload.get("spec_digest") or "") == current_spec_digest
                and str(payload.get("source_digest") or "") == current_source_digest
                for payload in (
                    _decode(event["payload_json"], {}) for event in frozen_events
                )
            )
            if not current_spec_frozen:
                raise ValueError("current_spec_freeze_required")
            spec_claims = {
                item["id"]
                for field in ("required_clauses", "negative_requirements")
                for item in (spec.get(field) or [])
                if isinstance(item, Mapping)
            }
            closure_criteria = set(spec.get("closure_criteria") or [])
            assigned_claim_items = [
                claim
                for definition in definitions
                for field in ("required_clauses", "negative_requirements")
                for claim in (definition.get(field) or [])
            ]
            if any(not _native_identity(claim) for claim in assigned_claim_items):
                raise ValueError("slice_claim_identity_invalid")
            assigned_claims = set(assigned_claim_items)
            if not assigned_claims.issubset(spec_claims):
                raise ValueError("slice_claim_not_in_frozen_spec")
            if not closure_criteria.issubset(assigned_claims):
                raise ValueError("plan_closure_criteria_unassigned")
            if str((spec.get("governance") or {}).get("target_profile") or "") == "strict_ui":
                spec_claims_by_id = {
                    item["id"]: item
                    for field in ("required_clauses", "negative_requirements")
                    for item in (spec.get(field) or [])
                    if isinstance(item, Mapping)
                }
                for definition in definitions:
                    definition_claim_ids = {
                        claim
                        for field in ("required_clauses", "negative_requirements")
                        for claim in (definition.get(field) or [])
                    }
                    claim_contracts = [
                        spec_claims_by_id[claim_id]
                        for claim_id in definition_claim_ids
                        if claim_id in spec_claims_by_id
                    ]
                    expected_obligations = {
                        str(ref)
                        for claim in claim_contracts
                        for ref in (claim.get("obligation_refs") or [])
                    }
                    expected_anchors = {
                        _json(anchor)
                        for claim in claim_contracts
                        for anchor in (claim.get("source_anchors") or [])
                        if isinstance(anchor, Mapping)
                    }
                    expected_oracles = {
                        str(ref)
                        for claim in claim_contracts
                        for ref in (claim.get("oracle_refs") or [])
                    }
                    expected_dimensions = {
                        str(ref)
                        for claim in claim_contracts
                        for ref in (claim.get("fidelity_dimensions") or [])
                    }
                    actual_anchors = definition.get("source_anchors")
                    if (
                        not isinstance(definition.get("obligation_refs"), list)
                        or set(definition.get("obligation_refs") or [])
                        != expected_obligations
                        or not isinstance(actual_anchors, list)
                        or {
                            _json(anchor)
                            for anchor in actual_anchors
                            if isinstance(anchor, Mapping)
                        }
                        != expected_anchors
                        or len(actual_anchors)
                        != len(expected_anchors)
                        or not isinstance(definition.get("oracle_refs"), list)
                        or set(definition.get("oracle_refs") or []) != expected_oracles
                        or not isinstance(definition.get("fidelity_dimensions"), list)
                        or set(definition.get("fidelity_dimensions") or [])
                        != expected_dimensions
                    ):
                        raise ValueError("slice_semantic_fidelity_contract_mismatch")
            source_ids = {
                _source_record_identity(item)
                for item in _decode(row["original_sources_json"], [])
                if isinstance(item, Mapping)
            }
            source_ids.update(
                event["event_id"]
                for event in conn.execute(
                    "SELECT event_id FROM source_events WHERE task_id = ?", (task_id,)
                ).fetchall()
            )
            for definition in definitions:
                refs_raw = definition.get("source_refs")
                if not isinstance(refs_raw, list) or any(
                    type(item) is not str or not item.strip() for item in refs_raw
                ):
                    raise ValueError("slice_source_ref_unknown")
                refs = set(refs_raw)
                if not refs or not refs.issubset(source_ids):
                    raise ValueError("slice_source_ref_unknown")
                commands = definition.get("verification_commands")
                if not isinstance(commands, list) or not commands or any(
                    not str(command or "").strip() for command in commands
                ):
                    raise ValueError("slice_verification_commands_required")
                owned = {str(item) for item in definition.get("owned_paths") or []}
                forbidden = {str(item) for item in definition.get("forbidden_changes") or []}
                if owned & forbidden:
                    raise ValueError("slice_owned_and_forbidden_paths_overlap")
                worker_command = definition.get("worker_command") or []
                if (
                    not isinstance(worker_command, list)
                    or any(
                        type(item) is not str or not item
                        for item in worker_command
                    )
                ):
                    raise ValueError("worker_command_invalid")
                worker_timeout = float(
                    definition.get("worker_timeout_seconds") or 900
                )
                if worker_timeout <= 0 or worker_timeout > 86_400:
                    raise ValueError("worker_timeout_seconds_invalid")
            self._invalidate_claims(conn, task_id, "plan_changed")
            conn.execute("DELETE FROM task_slices WHERE task_id = ?", (task_id,))
            for ordinal, definition in enumerate(definitions):
                conn.execute(
                    """
                    INSERT INTO task_slices (
                        task_id, slice_id, ordinal, definition_json, status, lease_owner,
                        lease_token, lease_expires_at, result_id, integrated_state_revision
                    ) VALUES (?, ?, ?, ?, 'pending', '', '', NULL, '', NULL)
                    """,
                    (task_id, definition["slice_id"], ordinal, _json(definition)),
                )
            conn.execute(
                """
                UPDATE tasks SET implementation_graph_json = ?, plan_revision = plan_revision + 1,
                    state_revision = state_revision + 1, status = 'planned', candidate_digest = '',
                    evidence_graph_json = '[]', closure_json = '{}', updated_at = ? WHERE task_id = ?
                """,
                (_json(definitions), _utc_now(), task_id),
            )
            conn.execute("UPDATE context_packets SET status = 'invalidated' WHERE task_id = ? AND status = 'active'", (task_id,))
            self._record_state_event(
                conn,
                task_id,
                int(row["state_revision"]) + 1,
                "plan_set",
                {
                    "plan_revision": int(row["plan_revision"]) + 1,
                    "slice_ids": [item["slice_id"] for item in definitions],
                },
            )
        return self.get_task(task_id)

    def claim_next_slice(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        lease_seconds: int = 300,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        now_value = float(time.time() if now is None else now)
        if lease_seconds <= 0:
            raise ValueError("lease_seconds_must_be_positive")
        if not _native_identity(worker_session_id):
            raise ValueError("worker_session_id_required")
        worker = worker_session_id.strip()
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            if str(task["status"]) in {"draft", "closed"}:
                raise ValueError("task_not_claimable")
            spec = _decode(task["spec_bundle_json"], {})
            source_snapshot = self._source_snapshot(conn, task)
            self._validate_governed_spec(
                spec,
                source_ids=self._source_ids(source_snapshot),
                source_snapshot=source_snapshot,
            )
            approved_review = conn.execute(
                "SELECT 1 FROM spec_reviews WHERE task_id = ? AND spec_digest = ? "
                "AND source_digest = ? AND verdict = 'approved' LIMIT 1",
                (task_id, _digest(spec), _digest(source_snapshot)),
            ).fetchone()
            if approved_review is None:
                raise ValueError("independent_spec_review_required")
            admission_events = conn.execute(
                "SELECT state_revision, event_type, payload_json FROM task_state_events "
                "WHERE task_id = ? AND event_type IN ('spec_frozen', 'plan_set') "
                "ORDER BY state_revision",
                (task_id,),
            ).fetchall()
            current_spec_revision = int(task["spec_revision"])
            current_plan_revision = int(task["plan_revision"])
            frozen_revisions = [
                int(event["state_revision"])
                for event in admission_events
                if str(event["event_type"]) == "spec_frozen"
                and int(_decode(event["payload_json"], {}).get("spec_revision") or -1)
                == current_spec_revision
                and str(_decode(event["payload_json"], {}).get("spec_digest") or "")
                == _digest(spec)
                and str(_decode(event["payload_json"], {}).get("source_digest") or "")
                == _digest(source_snapshot)
            ]
            latest_frozen_revision = max(frozen_revisions, default=-1)
            current_plan_admitted = any(
                str(event["event_type"]) == "plan_set"
                and int(event["state_revision"]) > latest_frozen_revision
                and int(_decode(event["payload_json"], {}).get("plan_revision") or -1)
                == current_plan_revision
                for event in admission_events
            )
            if latest_frozen_revision < 0:
                raise ValueError("current_spec_plan_admission_required")
            if (
                str(task["status"]) == "specified"
                and _decode(task["implementation_graph_json"], []) == []
            ):
                return None
            if not current_plan_admitted:
                raise ValueError("current_spec_plan_admission_required")
            expired_slice_ids = self._expire_leases(conn, task_id, now_value)
            if expired_slice_ids:
                task = self._task_row(conn, task_id)
            rows = conn.execute(
                "SELECT * FROM task_slices WHERE task_id = ? ORDER BY ordinal, slice_id",
                (task_id,),
            ).fetchall()
            statuses = {row["slice_id"]: row["status"] for row in rows}
            selected = None
            definition: dict[str, Any] = {}
            for row in rows:
                if row["status"] != "pending":
                    continue
                candidate = _decode(row["definition_json"], {})
                dependencies = [str(item) for item in candidate.get("dependencies") or []]
                if all(statuses.get(dep) == "completed" for dep in dependencies):
                    selected = row
                    definition = candidate
                    break
            if selected is None:
                return None
            lease_token = uuid.uuid4().hex
            expires = now_value + int(lease_seconds)
            state_revision = int(task["state_revision"]) + 1
            packet_id = uuid.uuid4().hex
            claim_contracts = _strict_ui_claim_contracts(spec)
            assigned_claim_ids = list(definition.get("required_clauses") or []) + list(
                definition.get("negative_requirements") or []
            )
            packet = {
                "schema": CONTEXT_PACKET_SCHEMA,
                "packet_id": packet_id,
                "task_id": task_id,
                "spec_revision": int(task["spec_revision"]),
                "plan_revision": int(task["plan_revision"]),
                "state_revision": state_revision,
                "slice_id": selected["slice_id"],
                "role": definition.get("role", "implementer"),
                "objective": definition.get("objective", ""),
                "required_clauses": list(definition.get("required_clauses") or []),
                "negative_requirements": list(definition.get("negative_requirements") or []),
                "source_refs": list(definition.get("source_refs") or []),
                "obligation_refs": list(definition.get("obligation_refs") or []),
                "source_anchors": list(definition.get("source_anchors") or []),
                "oracle_refs": list(definition.get("oracle_refs") or []),
                "fidelity_dimensions": list(
                    definition.get("fidelity_dimensions") or []
                ),
                "claim_contracts": {
                    claim_id: claim_contracts[claim_id]
                    for claim_id in assigned_claim_ids
                    if claim_id in claim_contracts
                },
                "source_digest": _digest(source_snapshot),
                "spec_digest": _digest(spec),
                "architecture_invariants": list(definition.get("architecture_invariants") or []),
                "owned_paths": list(definition.get("owned_paths") or []),
                "workspace_path": str(definition.get("workspace_path") or ""),
                "workspace_source_repo": str(definition.get("workspace_source_repo") or ""),
                "workspace_base_commit": str(definition.get("workspace_base_commit") or ""),
                "worker_command": list(definition.get("worker_command") or []),
                "worker_timeout_seconds": float(
                    definition.get("worker_timeout_seconds") or 900
                ),
                "forbidden_changes": list(definition.get("forbidden_changes") or []),
                "dependencies": list(definition.get("dependencies") or []),
                "verification_commands": list(definition.get("verification_commands") or []),
                "evidence_sink": {"task_id": task_id, "slice_id": selected["slice_id"]},
                "authority": dict(definition.get("authority") or {}),
                "lease": {"owner": worker, "token": lease_token, "expires_at": expires},
            }
            packet["packet_digest"] = _digest(packet)
            conn.execute(
                """
                UPDATE task_slices SET status = 'claimed', lease_owner = ?, lease_token = ?,
                    lease_expires_at = ? WHERE task_id = ? AND slice_id = ? AND status = 'pending'
                """,
                (worker, lease_token, expires, task_id, selected["slice_id"]),
            )
            conn.execute(
                """
                INSERT INTO context_packets (
                    packet_id, task_id, slice_id, packet_digest, payload_json,
                    spec_revision, plan_revision, state_revision, lease_owner,
                    lease_token, lease_expires_at, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                """,
                (
                    packet_id, task_id, selected["slice_id"], packet["packet_digest"],
                    _json(packet), packet["spec_revision"], packet["plan_revision"],
                    packet["state_revision"], worker, lease_token, expires, _utc_now(),
                ),
            )
            conn.execute(
                "UPDATE tasks SET state_revision = ?, status = 'running', updated_at = ? WHERE task_id = ?",
                (state_revision, _utc_now(), task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                state_revision,
                "slice_claimed",
                {
                    "slice_id": selected["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "expired_slice_ids": expired_slice_ids,
                },
            )
            return packet

    def verify_context_packet(self, packet: Mapping[str, Any]) -> bool:
        if not _native_json_value(packet):
            return False
        payload = dict(packet)
        expected = payload.pop("packet_digest", "")
        return _native_identity(expected) and expected == _digest(payload)

    def heartbeat_claim(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        lease_seconds: int,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Extend one exact active lease without minting a new ContextPacket."""
        if not _native_identity(worker_session_id):
            raise ValueError("worker_session_id_required")
        worker = worker_session_id.strip()
        if int(lease_seconds) <= 0:
            raise ValueError("lease_seconds_invalid")
        expires_at = float(time.time() if now is None else now) + int(lease_seconds)
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "active":
                raise ValueError("packet_not_active")
            if packet["lease_owner"] != worker:
                raise ValueError("lease_owner_mismatch")
            updated = conn.execute(
                """
                UPDATE task_slices SET lease_expires_at = ?
                WHERE task_id = ? AND slice_id = ? AND status = 'claimed'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (
                    expires_at,
                    task_id,
                    packet["slice_id"],
                    worker,
                    packet["lease_token"],
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            self._bump_state(
                conn,
                task_id,
                "slice_claim_heartbeat",
                {
                    "slice_id": packet["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "lease_token": packet["lease_token"],
                    "lease_expires_at": expires_at,
                },
            )
        return {
            "task_id": task_id,
            "packet_id": packet_id,
            "worker_session_id": worker,
            "lease_token": packet["lease_token"],
            "lease_expires_at": expires_at,
        }

    def release_claim(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        reason: str,
        termination_proof: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Abandon one active lease only after an auditable local termination observation."""
        if not _native_identity(worker_session_id):
            raise ValueError("worker_session_id_required")
        if not _native_identity(packet_id):
            raise ValueError("packet_id_required")
        worker = worker_session_id.strip()
        if not isinstance(termination_proof, Mapping):
            raise ValueError("termination_proof_required")
        proof = dict(termination_proof)
        allowed_proof_fields = {
            "kind", "worker_session_id", "packet_id", "lease_token", "observed_at"
        }
        if set(proof) - allowed_proof_fields:
            raise ValueError("termination_proof_unknown_fields")
        if type(proof.get("kind")) is not str or proof.get("kind") != "runner_not_started":
            raise ValueError("termination_proof_kind_invalid")
        if type(proof.get("worker_session_id")) is not str or proof.get("worker_session_id") != worker:
            raise ValueError("termination_proof_worker_mismatch")
        if type(proof.get("packet_id")) is not str or proof.get("packet_id") != packet_id:
            raise ValueError("termination_proof_packet_mismatch")
        if not _native_identity(proof.get("observed_at")):
            raise ValueError("termination_proof_observed_at_required")
        observed_at = proof["observed_at"].strip()
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("termination_proof_observed_at_invalid") from exc
        if observed.tzinfo is None or abs(time.time() - observed.timestamp()) > 300:
            raise ValueError("termination_proof_observed_at_stale")
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "active":
                raise ValueError("packet_not_active")
            if packet["lease_owner"] != worker:
                raise ValueError("lease_owner_mismatch")
            if str(proof.get("lease_token") or "") != str(packet["lease_token"]):
                raise ValueError("termination_proof_lease_fence_mismatch")
            updated = conn.execute(
                """
                UPDATE task_slices
                SET status = 'pending', lease_owner = '', lease_token = '',
                    lease_expires_at = NULL, result_id = ''
                WHERE task_id = ? AND slice_id = ? AND status = 'claimed'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (task_id, packet["slice_id"], worker, packet["lease_token"]),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            conn.execute(
                "UPDATE context_packets SET status = 'abandoned' WHERE packet_id = ?",
                (packet_id,),
            )
            self._bump_state(
                conn,
                task_id,
                "slice_claim_released",
                {
                    "slice_id": packet["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "reason": str(reason or "worker_failed"),
                    "termination_proof": proof,
                },
            )
        return {
            "task_id": task_id,
            "packet_id": packet_id,
            "worker_session_id": worker,
            "status": "abandoned",
            "reason": str(reason or "worker_failed"),
            "termination_proof": proof,
        }

    def quarantine_claim(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        reason: str,
    ) -> dict[str, Any]:
        """Fence an uncertain timed-out worker without making its workspace retryable."""
        if not _native_identity(worker_session_id):
            raise ValueError("worker_session_id_required")
        worker = worker_session_id.strip()
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "active":
                raise ValueError("packet_not_active")
            if packet["lease_owner"] != worker:
                raise ValueError("lease_owner_mismatch")
            updated = conn.execute(
                """
                UPDATE task_slices
                SET status = 'quarantined'
                WHERE task_id = ? AND slice_id = ? AND status = 'claimed'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (task_id, packet["slice_id"], worker, packet["lease_token"]),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            conn.execute(
                "UPDATE context_packets SET status = 'quarantined' WHERE packet_id = ?",
                (packet_id,),
            )
            self._bump_state(
                conn,
                task_id,
                "slice_claim_quarantined",
                {
                    "slice_id": packet["slice_id"],
                    "packet_id": packet_id,
                    "worker_session_id": worker,
                    "reason": str(reason or "worker_termination_unproven"),
                },
            )
        return {
            "task_id": task_id,
            "packet_id": packet_id,
            "worker_session_id": worker,
            "status": "quarantined",
            "reason": str(reason or "worker_termination_unproven"),
        }

    def recover_quarantined_claim(
        self,
        task_id: str,
        *,
        cleanup: Any,
        workspace_reap_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Retry a quarantined slice only from a controller-issued cleanup capability."""
        from agent.task_runtime_process import VerifiedTaskCleanup

        if not isinstance(cleanup, VerifiedTaskCleanup):
            raise ValueError("verified_process_cleanup_required")
        binding = cleanup.binding
        receipt = cleanup.receipt
        if binding.task_id != task_id:
            raise ValueError("cleanup_task_mismatch")
        if not (
            receipt.leader_reaped
            and receipt.residual_probe_succeeded
            and not receipt.residual_pids
        ):
            raise ValueError("cleanup_process_residuals_present")
        try:
            observed = datetime.fromisoformat(receipt.observed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("cleanup_observed_at_invalid") from exc
        if observed.tzinfo is None or abs(time.time() - observed.timestamp()) > 300:
            raise ValueError("cleanup_observed_at_stale")
        with self._write() as conn:
            self._task_row(conn, task_id)
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ? AND task_id = ?",
                (binding.packet_id, task_id),
            ).fetchone()
            if packet is None:
                raise ValueError("packet_not_found")
            if packet["status"] != "quarantined":
                raise ValueError("packet_not_quarantined")
            if packet["slice_id"] != binding.slice_id:
                raise ValueError("cleanup_slice_mismatch")
            if packet["lease_owner"] != binding.worker_session_id:
                raise ValueError("cleanup_worker_mismatch")
            if packet["lease_token"] != binding.lease_token:
                raise ValueError("cleanup_lease_fence_mismatch")
            packet_payload = _decode(packet["payload_json"], {})
            auto_workspace = bool(
                packet_payload.get("workspace_source_repo")
                and not packet_payload.get("workspace_path")
                and packet_payload.get("owned_paths")
            )
            if auto_workspace:
                if not isinstance(workspace_reap_receipt, Mapping):
                    raise ValueError("workspace_reap_receipt_required")
                workspace_receipt = dict(workspace_reap_receipt)
                if (
                    workspace_receipt.get("packet_id") != binding.packet_id
                    or workspace_receipt.get("lease_token") != binding.lease_token
                ):
                    raise ValueError("workspace_reap_fence_mismatch")
                if not (
                    workspace_receipt.get("removed") is True
                    and workspace_receipt.get("residual_probe_succeeded") is True
                    and not workspace_receipt.get("residual_writer_pids")
                ):
                    raise ValueError("workspace_reap_unproven")
            updated = conn.execute(
                """
                UPDATE task_slices
                SET status = 'pending', lease_owner = '', lease_token = '',
                    lease_expires_at = NULL, result_id = ''
                WHERE task_id = ? AND slice_id = ? AND status = 'quarantined'
                    AND lease_owner = ? AND lease_token = ?
                """,
                (
                    task_id,
                    binding.slice_id,
                    binding.worker_session_id,
                    binding.lease_token,
                ),
            )
            if updated.rowcount != 1:
                raise ValueError("lease_fence_mismatch")
            conn.execute(
                "UPDATE context_packets SET status = 'recovered' WHERE packet_id = ?",
                (binding.packet_id,),
            )
            self._bump_state(
                conn,
                task_id,
                "slice_claim_recovered",
                {
                    "slice_id": binding.slice_id,
                    "packet_id": binding.packet_id,
                    "worker_session_id": binding.worker_session_id,
                    "lease_token": binding.lease_token,
                    "process_receipt": receipt.to_dict(),
                    "workspace_reap_receipt": (
                        dict(workspace_reap_receipt)
                        if workspace_reap_receipt is not None
                        else None
                    ),
                },
            )
        return {
            "task_id": task_id,
            "slice_id": binding.slice_id,
            "packet_id": binding.packet_id,
            "status": "recovered",
        }

    def submit_result(
        self,
        task_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        result: Mapping[str, Any],
        now: float | None = None,
    ) -> dict[str, Any]:
        native_result = _native_json_value(result)
        payload = dict(result) if type(result) is dict else {}
        worker = str(worker_session_id or "")
        now_value = float(time.time() if now is None else now)
        claims_value = payload.get("claims")
        evidence_value = payload.get("evidence_refs")
        required_fields = {
            "claims", "changed_artifacts", "evidence_refs", "verification_results",
            "risks", "unknowns", "state_delta_proposal", "candidate_digest",
        }
        allowed_fields = required_fields | {"slice_id", "input_digest"}
        list_fields = (
            "claims",
            "changed_artifacts",
            "evidence_refs",
            "verification_results",
            "risks",
            "unknowns",
        )
        worker_result_container_invalid = (
            not native_result
            or any(type(payload.get(field)) is not list for field in list_fields)
            or type(payload.get("state_delta_proposal")) is not dict
        )
        worker_result_schema_invalid = (
            bool(set(payload) - allowed_fields)
            or any(field not in payload for field in required_fields)
            or worker_result_container_invalid
        )
        governed_identity_invalid = (
            not _native_identity(worker_session_id)
            or not _native_identity(packet_id)
            or not _native_identity(payload.get("candidate_digest"))
            or any(
                field in payload and not _native_identity(payload.get(field))
                for field in ("slice_id", "input_digest")
            )
            or (
                isinstance(evidence_value, list)
                and any(
                    isinstance(item, Mapping)
                    and any(
                        field in item and not _native_identity(item.get(field))
                        for field in ("candidate_digest", "source_digest", "spec_digest")
                    )
                    for item in evidence_value
                )
            )
        )
        native_identity_invalid = worker_result_schema_invalid or governed_identity_invalid or (
            isinstance(claims_value, list)
            and any(type(item) is not str or not item.strip() for item in claims_value)
        ) or (
            isinstance(evidence_value, list)
            and any(
                isinstance(item, Mapping)
                and "claim_id" in item
                and (
                    type(item.get("claim_id")) is not str
                    or not str(item.get("claim_id") or "").strip()
                )
                for item in evidence_value
            )
        )
        native_identity_types: dict[str, Any] = {}
        if native_identity_invalid:
            native_identity_types = {
                "worker_session_id": type(worker_session_id).__qualname__,
                "packet_id": type(packet_id).__qualname__,
                "candidate_digest": type(payload.get("candidate_digest")).__qualname__,
                "claims": [type(item).__qualname__ for item in claims_value]
                if isinstance(claims_value, list)
                else [],
                "evidence_claims": [
                    type(item.get("claim_id")).__qualname__
                    for item in evidence_value
                    if isinstance(item, Mapping) and "claim_id" in item
                ]
                if isinstance(evidence_value, list)
                else [],
                "field_types": {
                    field: type(payload.get(field)).__qualname__
                    for field in sorted(required_fields)
                },
                "non_native_json_paths": _non_native_json_paths(result),
            }
            identity = "result_" + _digest(
                {
                    "worker_session_id": worker
                    if _native_identity(worker_session_id)
                    else type(worker_session_id).__qualname__,
                    "packet_id": packet_id
                    if _native_identity(packet_id)
                    else type(packet_id).__qualname__,
                    "candidate_digest": payload.get("candidate_digest")
                    if _native_identity(payload.get("candidate_digest"))
                    else type(payload.get("candidate_digest")).__qualname__,
                    "native_identity_types": native_identity_types,
                }
            ).split(":", 1)[1][:24]
        else:
            identity = _result_id(packet_id, worker, payload)
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            existing = conn.execute("SELECT * FROM worker_results WHERE result_id = ?", (identity,)).fetchone()
            if existing:
                return self._result_payload(existing, idempotent_replay=True)
            packet = conn.execute("SELECT * FROM context_packets WHERE packet_id = ?", (packet_id,)).fetchone()
            reasons: list[str] = []
            if governed_identity_invalid:
                reasons.append("worker_result_identity_invalid")
            if worker_result_container_invalid:
                reasons.append("worker_result_invalid_field_types")
            if packet is None:
                reasons.append("packet_not_found")
                slice_id = str(payload.get("slice_id") or "")
                packet_payload: dict[str, Any] = {}
            else:
                slice_id = packet["slice_id"]
                packet_payload = _decode(packet["payload_json"], {})
                prior_for_packet = conn.execute(
                    """
                    SELECT result_id FROM worker_results
                    WHERE task_id = ? AND packet_id = ?
                        AND disposition IN ('accepted_for_integration', 'integrated')
                    LIMIT 1
                    """,
                    (task_id, packet_id),
                ).fetchone()
                if prior_for_packet is not None and prior_for_packet["result_id"] != identity:
                    reasons.append("conflicting_result_for_packet")
                if packet["task_id"] != task_id:
                    reasons.append("packet_task_mismatch")
                if int(packet["spec_revision"]) != int(task["spec_revision"]):
                    reasons.append("stale_spec_revision")
                if int(packet["plan_revision"]) != int(task["plan_revision"]):
                    reasons.append("stale_plan_revision")
                if packet["status"] != "active":
                    reasons.append(f"packet_not_active:{packet['status']}")
                if packet["lease_owner"] != worker:
                    reasons.append("lease_owner_mismatch")
                slice_attempt = conn.execute(
                    "SELECT status, lease_owner, lease_token FROM task_slices "
                    "WHERE task_id = ? AND slice_id = ?",
                    (task_id, slice_id),
                ).fetchone()
                if slice_attempt is None:
                    reasons.append("slice_not_found")
                else:
                    if slice_attempt["status"] != "claimed":
                        reasons.append("slice_not_claimed")
                    if slice_attempt["lease_owner"] != worker:
                        reasons.append("current_lease_owner_mismatch")
                    if slice_attempt["lease_token"] != packet["lease_token"]:
                        reasons.append("current_lease_fence_mismatch")
                if now_value > float(packet["lease_expires_at"]):
                    reasons.append("lease_expired")
                if payload.get("input_digest") != packet["packet_digest"]:
                    reasons.append("input_digest_mismatch")
                if payload.get("slice_id") != slice_id:
                    reasons.append("slice_id_mismatch")
                if not self.verify_context_packet(packet_payload):
                    reasons.append("stored_packet_digest_invalid")
                owned = {str(path) for path in packet_payload.get("owned_paths") or []}
                forbidden = {str(path) for path in packet_payload.get("forbidden_changes") or []}
                changed = {str(path) for path in payload.get("changed_artifacts") or []}
                if not changed.issubset(owned):
                    reasons.append("changed_artifact_outside_owned_paths")
                if changed & forbidden:
                    reasons.append("forbidden_change_reported")
                if set(payload) - allowed_fields:
                    reasons.append("worker_result_unknown_fields")
                if any(field not in payload for field in required_fields):
                    reasons.append("worker_result_missing_required_fields")
                else:
                    if any(type(payload.get(field)) is not list for field in list_fields):
                        reasons.append("worker_result_invalid_field_types")
                    else:
                        if any(
                            type(item) is not str or not item.strip()
                            for item in payload["claims"]
                        ):
                            reasons.append("worker_claims_invalid")
                        claims = {
                            item
                            for item in payload["claims"]
                            if type(item) is str and item.strip()
                        }
                        assigned_claims = {
                            str(item)
                            for field in ("required_clauses", "negative_requirements")
                            for item in (packet_payload.get(field) or [])
                        }
                        if not assigned_claims.issubset(claims):
                            reasons.append("worker_claims_incomplete")
                        if not claims.issubset(assigned_claims):
                            reasons.append("worker_claims_unassigned")
                        evidence_refs = payload["evidence_refs"]
                        if not evidence_refs or any(
                            not isinstance(item, Mapping)
                            or not _native_identity(item.get("ref"))
                            for item in evidence_refs
                        ):
                            reasons.append("worker_evidence_refs_invalid")
                        else:
                            strict_evidence = bool(packet_payload.get("obligation_refs"))
                            allowed_evidence_fields = {"kind", "ref"}
                            if strict_evidence:
                                allowed_evidence_fields |= {
                                    "claim_id",
                                    "obligation_refs",
                                    "oracle_refs",
                                    "fidelity_dimensions",
                                    "source_anchors",
                                    "candidate_digest",
                                    "source_digest",
                                    "spec_digest",
                                }
                            if any(
                                set(item) - allowed_evidence_fields
                                for item in evidence_refs
                                if isinstance(item, Mapping)
                            ):
                                reasons.append("worker_evidence_refs_unknown_fields")
                            elif strict_evidence:
                                packet_obligations = set(
                                    packet_payload.get("obligation_refs") or []
                                )
                                packet_oracles = set(packet_payload.get("oracle_refs") or [])
                                packet_dimensions = set(
                                    packet_payload.get("fidelity_dimensions") or []
                                )
                                if any(
                                    not isinstance(item.get("obligation_refs"), list)
                                    or not set(item.get("obligation_refs") or [])
                                    or not set(item.get("obligation_refs") or []).issubset(
                                        packet_obligations
                                    )
                                    or not isinstance(item.get("oracle_refs"), list)
                                    or not set(item.get("oracle_refs") or [])
                                    or not set(item.get("oracle_refs") or []).issubset(
                                        packet_oracles
                                    )
                                    or not isinstance(item.get("fidelity_dimensions"), list)
                                    or not set(item.get("fidelity_dimensions") or [])
                                    or not set(item.get("fidelity_dimensions") or []).issubset(
                                        packet_dimensions
                                    )
                                    or type(item.get("claim_id")) is not str
                                    or not str(item.get("claim_id") or "").strip()
                                    or item.get("claim_id") not in assigned_claims
                                    or item.get("candidate_digest")
                                    != payload.get("candidate_digest")
                                    or item.get("source_digest")
                                    != packet_payload.get("source_digest")
                                    or item.get("spec_digest")
                                    != packet_payload.get("spec_digest")
                                    for item in evidence_refs
                                ):
                                    reasons.append("worker_semantic_evidence_incomplete")
                                elif any(
                                    not _claim_evidence_contract_matches(
                                        item, packet_payload.get("claim_contracts") or {}
                                    )
                                    for item in evidence_refs
                                ):
                                    reasons.append(
                                        "worker_semantic_evidence_claim_contract_mismatch"
                                    )
                                elif {
                                    item.get("claim_id")
                                    for item in evidence_refs
                                } != assigned_claims:
                                    reasons.append(
                                        "worker_semantic_evidence_claim_binding_incomplete"
                                    )
                        verification_results = payload["verification_results"]
                        expected_commands = {
                            str(item) for item in packet_payload.get("verification_commands") or []
                        }
                        observed_commands = {
                            str(item.get("command") or "")
                            for item in verification_results
                            if isinstance(item, Mapping)
                        }
                        if (
                            not verification_results
                            or any(not isinstance(item, Mapping) for item in verification_results)
                            or not expected_commands.issubset(observed_commands)
                        ):
                            reasons.append("worker_verification_incomplete")
                        elif any(
                            set(item) - {"command", "exit_code"}
                            for item in verification_results
                            if isinstance(item, Mapping)
                        ):
                            reasons.append("worker_verification_unknown_fields")
                        elif any(
                            type(item.get("exit_code")) is not int
                            or int(item["exit_code"]) != 0
                            for item in verification_results
                        ):
                            reasons.append("worker_verification_failed")
                        if payload.get("state_delta_proposal") != {"slice_status": "completed"}:
                            reasons.append("worker_state_delta_invalid")
                        if not str(payload.get("candidate_digest") or "").strip():
                            reasons.append("candidate_digest_required")
            disposition = "rejected" if reasons else "accepted_for_integration"
            if not native_result:
                return {
                    "result_id": identity,
                    "task_id": task_id,
                    "slice_id": slice_id if type(slice_id) is str else "",
                    "packet_id": packet_id if type(packet_id) is str else "",
                    "worker_session_id": worker,
                    "payload_digest": _digest(native_identity_types),
                    "payload": {},
                    "disposition": disposition,
                    "reasons": reasons,
                    "integrator_session_id": "",
                    "integrated_state_revision": None,
                    "created_at": "",
                    "updated_at": "",
                    "idempotent_replay": False,
                }
            now_iso = _utc_now()
            conn.execute(
                """
                INSERT INTO worker_results (
                    result_id, task_id, slice_id, packet_id, worker_session_id,
                    payload_digest, payload_json, disposition, reasons_json,
                    integrator_session_id, integrated_state_revision, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '', NULL, ?, ?)
                """,
                (
                    identity, task_id, slice_id, packet_id, worker, _digest(payload),
                    _json(payload), disposition, _json(reasons), now_iso, now_iso,
                ),
            )
            if not reasons and packet is not None:
                conn.execute("UPDATE context_packets SET status = 'submitted' WHERE packet_id = ?", (packet_id,))
                conn.execute(
                    "UPDATE task_slices SET status = 'submitted', result_id = ? WHERE task_id = ? AND slice_id = ?",
                    (identity, task_id, slice_id),
                )
            self._bump_state(
                conn,
                task_id,
                "worker_result_submitted",
                {
                    "result_id": identity,
                    "packet_id": packet_id,
                    "slice_id": slice_id,
                    "disposition": disposition,
                    "reasons": reasons,
                },
            )
            row = conn.execute("SELECT * FROM worker_results WHERE result_id = ?", (identity,)).fetchone()
            return self._result_payload(row, idempotent_replay=False)

    def quarantine_candidate_finalize_failure(
        self,
        task_id: str,
        result_id: str,
        *,
        worker_session_id: str,
        packet_id: str,
        error: str,
    ) -> dict[str, Any]:
        """Fail closed after an accepted result cannot be frozen into a candidate.

        The accepted result is retained for audit but made non-integrable, while the
        exact packet and slice move to quarantine.  The managed workspace is left in
        place for controller repair; it is never released as a retryable claim.
        """
        with self._write() as conn:
            row = conn.execute(
                "SELECT * FROM worker_results WHERE result_id = ? AND task_id = ?",
                (result_id, task_id),
            ).fetchone()
            if row is None:
                raise KeyError(result_id)
            if row["packet_id"] != packet_id:
                raise ValueError("candidate_finalize_packet_mismatch")
            if row["worker_session_id"] != worker_session_id:
                raise ValueError("candidate_finalize_worker_mismatch")
            if row["disposition"] == "candidate_finalize_failed":
                return {
                    "task_id": task_id,
                    "result_id": result_id,
                    "slice_id": row["slice_id"],
                    "status": "quarantined",
                    "idempotent_replay": True,
                }
            if row["disposition"] != "accepted_for_integration":
                raise ValueError("candidate_finalize_result_not_accepted")
            packet = conn.execute(
                "SELECT * FROM context_packets WHERE packet_id = ?",
                (packet_id,),
            ).fetchone()
            if packet is None or packet["status"] != "submitted":
                raise ValueError("candidate_finalize_packet_not_submitted")
            slice_row = conn.execute(
                "SELECT * FROM task_slices WHERE task_id = ? AND slice_id = ?",
                (task_id, row["slice_id"]),
            ).fetchone()
            if slice_row is None or slice_row["status"] != "submitted":
                raise ValueError("candidate_finalize_slice_not_submitted")
            reason = f"workspace_candidate_finalize_failed:{str(error)[:500]}"
            now_iso = _utc_now()
            conn.execute(
                "UPDATE worker_results SET disposition = 'candidate_finalize_failed', "
                "reasons_json = ?, updated_at = ? WHERE result_id = ?",
                (_json([reason]), now_iso, result_id),
            )
            conn.execute(
                "UPDATE context_packets SET status = 'quarantined' WHERE packet_id = ?",
                (packet_id,),
            )
            conn.execute(
                "UPDATE task_slices SET status = 'quarantined' "
                "WHERE task_id = ? AND slice_id = ?",
                (task_id, row["slice_id"]),
            )
            self._bump_state(
                conn,
                task_id,
                "workspace_candidate_finalize_failed",
                {
                    "result_id": result_id,
                    "packet_id": packet_id,
                    "slice_id": row["slice_id"],
                    "worker_session_id": worker_session_id,
                    "reason": reason,
                },
            )
            return {
                "task_id": task_id,
                "result_id": result_id,
                "slice_id": row["slice_id"],
                "status": "quarantined",
                "idempotent_replay": False,
            }

    def integrate_result(
        self,
        task_id: str,
        result_id: str,
        *,
        integrator_session_id: str,
        workspace_candidate_receipt: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not _native_identity(result_id):
            raise ValueError("result_id_required")
        if not _native_identity(integrator_session_id):
            raise ValueError("integrator_session_id_required")
        integrator = integrator_session_id.strip()
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            row = conn.execute("SELECT * FROM worker_results WHERE result_id = ? AND task_id = ?", (result_id, task_id)).fetchone()
            if row is None:
                raise KeyError(result_id)
            if row["disposition"] == "integrated":
                slice_row = conn.execute("SELECT * FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, row["slice_id"])).fetchone()
                return {"task_id": task_id, "result_id": result_id, "slice_id": row["slice_id"], "slice_status": slice_row["status"], "idempotent_replay": True}
            if row["disposition"] != "accepted_for_integration":
                raise ValueError("result_not_accepted_for_integration")
            payload = _decode(row["payload_json"], {})
            slice_row = conn.execute("SELECT * FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, row["slice_id"])).fetchone()
            definition = _decode(slice_row["definition_json"], {})
            dependencies = list(definition.get("dependencies") or [])
            for dependency in dependencies:
                dep = conn.execute("SELECT status FROM task_slices WHERE task_id = ? AND slice_id = ?", (task_id, dependency)).fetchone()
                if dep is None or dep["status"] != "completed":
                    raise ValueError("dependency_not_completed")
            packet = conn.execute(
                "SELECT state_revision, lease_token, payload_json FROM context_packets "
                "WHERE packet_id = ?",
                (row["packet_id"],),
            ).fetchone()
            changed = {str(path) for path in payload.get("changed_artifacts") or []}
            prior_rows = conn.execute(
                "SELECT payload_json FROM worker_results WHERE task_id = ? AND disposition = 'integrated' "
                "AND integrated_state_revision > ?",
                (task_id, int(packet["state_revision"])),
            ).fetchall()
            prior_changed = {
                str(path)
                for prior in prior_rows
                for path in (_decode(prior["payload_json"], {}).get("changed_artifacts") or [])
            }
            if changed & prior_changed:
                raise ValueError("write_set_conflict_since_packet")
            candidate = str(payload.get("candidate_digest") or "")
            if not candidate:
                raise ValueError("candidate_digest_required")
            managed_workspace = bool(
                definition.get("owned_paths")
                and str(definition.get("workspace_source_repo") or "").strip()
                and not str(definition.get("workspace_path") or "").strip()
            )
            verified_workspace_receipt = None
            workspace_integration_receipt = None
            if managed_workspace:
                verified_workspace_receipt = _verify_workspace_candidate_receipt(
                    workspace_candidate_receipt,
                    task_id=task_id,
                    slice_id=str(row["slice_id"]),
                    packet_id=str(row["packet_id"]),
                    lease_token=str(packet["lease_token"]),
                    source_repo=str(definition.get("workspace_source_repo") or ""),
                    base_commit=str(definition.get("workspace_base_commit") or ""),
                    candidate_digest=candidate,
                    changed_artifacts=changed,
                )
                workspace_integration_receipt = _integrate_workspace_candidate_ref(
                    verified_workspace_receipt,
                    integrator_session_id=integrator,
                )
            new_revision = int(task["state_revision"]) + 1
            evidence = _decode(task["evidence_graph_json"], [])
            evidence.append(
                {
                    "result_id": result_id,
                    "slice_id": row["slice_id"],
                    "claims": list(payload.get("claims") or []),
                    "evidence_refs": list(payload.get("evidence_refs") or []),
                    "verification_results": list(payload.get("verification_results") or []),
                    "candidate_digest": candidate,
                    "workspace_candidate_receipt": verified_workspace_receipt,
                    "workspace_integration_receipt": workspace_integration_receipt,
                    "integrated_state_revision": new_revision,
                }
            )
            now = _utc_now()
            conn.execute(
                "UPDATE worker_results SET disposition = 'integrated', integrator_session_id = ?, "
                "integrated_state_revision = ?, updated_at = ? WHERE result_id = ?",
                (integrator, new_revision, now, result_id),
            )
            conn.execute(
                "UPDATE task_slices SET status = 'completed', integrated_state_revision = ? "
                "WHERE task_id = ? AND slice_id = ?",
                (new_revision, task_id, row["slice_id"]),
            )
            conn.execute(
                "UPDATE tasks SET evidence_graph_json = ?, candidate_digest = ?, state_revision = ?, "
                "status = 'running', updated_at = ? WHERE task_id = ?",
                (_json(evidence), candidate, new_revision, now, task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                new_revision,
                "worker_result_integrated",
                {
                    "result_id": result_id,
                    "slice_id": row["slice_id"],
                    "candidate_digest": candidate,
                    "workspace_candidate_receipt": verified_workspace_receipt,
                    "workspace_integration_receipt": workspace_integration_receipt,
                    "integrator_session_id": integrator,
                },
            )
            return {
                "task_id": task_id,
                "result_id": result_id,
                "slice_id": row["slice_id"],
                "slice_status": "completed",
                "state_revision": new_revision,
                "candidate_digest": candidate,
                "workspace_integration_receipt": workspace_integration_receipt,
                "idempotent_replay": False,
            }

    def build_closure_packet(
        self,
        task_id: str,
        *,
        verifier_session_id: str,
    ) -> dict[str, Any]:
        """Build a digest-bound fresh closure context without mutating task state."""
        if not _native_identity(verifier_session_id):
            raise ValueError("closure_verifier_session_id_required")
        verifier = verifier_session_id.strip()
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            incomplete = conn.execute(
                "SELECT slice_id FROM task_slices WHERE task_id = ? AND status != 'completed'",
                (task_id,),
            ).fetchall()
            if incomplete:
                raise ValueError("task_slices_incomplete")
            participants = conn.execute(
                "SELECT worker_session_id, integrator_session_id FROM worker_results "
                "WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            participant_ids = {
                value
                for row in participants
                for value in (row["worker_session_id"], row["integrator_session_id"])
                if value
            }
            if not verifier or verifier in participant_ids:
                raise ValueError("closure_verifier_not_independent")
            candidate = str(task["candidate_digest"] or "").strip()
            if not candidate:
                raise ValueError("candidate_digest_required")
            source_events = [
                _decode(row["payload_json"], {})
                for row in conn.execute(
                    "SELECT payload_json FROM source_events WHERE task_id = ? ORDER BY rowid",
                    (task_id,),
                ).fetchall()
            ]
            spec = _decode(task["spec_bundle_json"], {})
            packet = {
                "schema": "hermes_closure_context_v1",
                "packet_id": uuid.uuid4().hex,
                "task_id": task_id,
                "owner_profile": str(task["owner_profile"]),
                "spec_revision": int(task["spec_revision"]),
                "plan_revision": int(task["plan_revision"]),
                "state_revision": int(task["state_revision"]),
                "candidate_digest": candidate,
                "verifier_session_id": verifier,
                "source_digest": _digest(
                    {
                        "original_sources": _decode(task["original_sources_json"], []),
                        "source_events": source_events,
                    }
                ),
                "spec_digest": _digest(spec),
                "implementation_graph_digest": _digest(
                    _decode(task["implementation_graph_json"], [])
                ),
                "evidence_graph_digest": _digest(
                    _decode(task["evidence_graph_json"], [])
                ),
                "closure_criteria": list(spec.get("closure_criteria") or []),
                "created_at": _utc_now(),
            }
            packet.update(_strict_ui_contract(spec))
            packet["packet_digest"] = _digest(packet)
            return packet

    def close_task(
        self,
        task_id: str,
        *,
        candidate_digest: str,
        verifier_session_id: str,
        claims: Mapping[str, str],
        closure_packet: Mapping[str, Any],
        evidence_refs: Sequence[Mapping[str, Any]],
        verification_results: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        if (
            not _native_identity(candidate_digest)
            or not _native_identity(verifier_session_id)
            or any(
                not isinstance(item, Mapping)
                or any(
                    field in item and not _native_identity(item.get(field))
                    for field in ("candidate_digest", "source_digest", "spec_digest")
                )
                for item in evidence_refs
            )
            or any(
                not isinstance(item, Mapping)
                or not _native_identity(item.get("candidate_digest"))
                for item in verification_results
            )
        ):
            raise ValueError("closure_identity_invalid")
        verifier = verifier_session_id.strip()
        if any(type(key) is not str or not key.strip() for key in claims):
            raise ValueError("closure_claim_identity_invalid")
        if any(
            "claim_id" in item
            and (
                type(item.get("claim_id")) is not str
                or not str(item.get("claim_id") or "").strip()
            )
            for item in evidence_refs
        ):
            raise ValueError("closure_evidence_claim_binding_invalid")
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            if (
                str(task["status"]) == "closed"
                and candidate_digest != task["candidate_digest"]
            ):
                raise ValueError("candidate_digest_mismatch")
            packet = dict(closure_packet or {})
            if any(
                not _native_identity(packet.get(field))
                for field in (
                    "packet_id",
                    "packet_digest",
                    "task_id",
                    "candidate_digest",
                    "verifier_session_id",
                    "source_digest",
                    "spec_digest",
                )
            ):
                raise ValueError("closure_identity_invalid")
            packet_digest = packet.pop("packet_digest")
            if not packet_digest or packet_digest != _digest(packet):
                raise ValueError("closure_packet_digest_invalid")
            if str(task["status"]) == "closed":
                existing = _decode(task["closure_json"], {})
                replay_matches = (
                    existing.get("candidate_digest") == candidate_digest
                    and str(existing.get("verifier_session_id") or "") == verifier
                    and str(existing.get("closure_packet_id") or "")
                    == str(packet.get("packet_id") or "")
                    and str(existing.get("closure_packet_digest") or "") == packet_digest
                    and existing.get("claims") == dict(claims)
                    and existing.get("evidence_refs")
                    == [dict(item) for item in evidence_refs]
                    and existing.get("verification_results")
                    == [dict(item) for item in verification_results]
                )
                if not replay_matches:
                    raise ValueError("task_already_closed_conflict")
                return self.get_task(task_id)
            source_events = [
                _decode(row["payload_json"], {})
                for row in conn.execute(
                    "SELECT payload_json FROM source_events WHERE task_id = ? ORDER BY rowid",
                    (task_id,),
                ).fetchall()
            ]
            spec = _decode(task["spec_bundle_json"], {})
            expected_packet_fields = {
                "schema": "hermes_closure_context_v1",
                "task_id": task_id,
                "owner_profile": str(task["owner_profile"]),
                "spec_revision": int(task["spec_revision"]),
                "plan_revision": int(task["plan_revision"]),
                "state_revision": int(task["state_revision"]),
                "candidate_digest": str(task["candidate_digest"]),
                "verifier_session_id": verifier,
                "source_digest": _digest(
                    {
                        "original_sources": _decode(task["original_sources_json"], []),
                        "source_events": source_events,
                    }
                ),
                "spec_digest": _digest(spec),
                "implementation_graph_digest": _digest(
                    _decode(task["implementation_graph_json"], [])
                ),
                "evidence_graph_digest": _digest(
                    _decode(task["evidence_graph_json"], [])
                ),
                "closure_criteria": list(spec.get("closure_criteria") or []),
            }
            expected_packet_fields.update(_strict_ui_contract(spec))
            if any(packet.get(key) != value for key, value in expected_packet_fields.items()):
                raise ValueError("closure_packet_stale")
            if candidate_digest != task["candidate_digest"]:
                raise ValueError("candidate_digest_mismatch")
            if not str(packet.get("packet_id") or "").strip() or not str(
                packet.get("created_at") or ""
            ).strip():
                raise ValueError("closure_packet_invalid")
            incomplete = conn.execute(
                "SELECT slice_id FROM task_slices WHERE task_id = ? AND status != 'completed'",
                (task_id,),
            ).fetchall()
            if incomplete:
                raise ValueError("task_slices_incomplete")
            participants = conn.execute(
                "SELECT worker_session_id, integrator_session_id FROM worker_results WHERE task_id = ?",
                (task_id,),
            ).fetchall()
            participant_ids = {
                value
                for row in participants
                for value in (row["worker_session_id"], row["integrator_session_id"])
                if value
            }
            if not verifier or verifier in participant_ids:
                raise ValueError("closure_verifier_not_independent")
            spec = _decode(task["spec_bundle_json"], {})
            required = {str(item) for item in spec.get("closure_criteria") or []}
            if any(type(key) is not str or not key.strip() for key in claims):
                raise ValueError("closure_claim_identity_invalid")
            if not set(claims).issubset(required):
                raise ValueError("closure_claims_unknown")
            passed = {key for key, value in claims.items() if type(value) is str and value == "pass"}
            if not required.issubset(passed):
                raise ValueError("closure_claims_incomplete")
            closure_evidence = [dict(item) for item in evidence_refs]
            closure_verification = [dict(item) for item in verification_results]
            candidate = candidate_digest
            semantic_contract = _strict_ui_contract(spec)
            claim_contracts = _strict_ui_claim_contracts(spec)
            if semantic_contract:
                semantic_contract.update(
                    source_digest=str(packet.get("source_digest") or ""),
                    spec_digest=str(packet.get("spec_digest") or ""),
                )
            allowed_evidence_fields = {"kind", "ref", "candidate_digest"}
            if semantic_contract:
                allowed_evidence_fields |= {
                    "claim_id",
                    "obligation_refs",
                    "oracle_refs",
                    "fidelity_dimensions",
                    "source_anchors",
                    "source_digest",
                    "spec_digest",
                }
            if not closure_evidence or any(
                not _native_identity(item.get("ref"))
                or item.get("candidate_digest") != candidate
                or bool(set(item) - allowed_evidence_fields)
                for item in closure_evidence
            ):
                raise ValueError("closure_evidence_required")
            if semantic_contract:
                observed_obligations = {
                    str(ref)
                    for item in closure_evidence
                    for ref in (item.get("obligation_refs") or [])
                }
                if observed_obligations != set(semantic_contract["obligation_refs"]):
                    raise ValueError("closure_obligation_evidence_incomplete")
                observed_oracles = {
                    str(ref)
                    for item in closure_evidence
                    for ref in (item.get("oracle_refs") or [])
                }
                if observed_oracles != set(semantic_contract["oracle_refs"]):
                    raise ValueError("closure_oracle_binding_invalid")
                observed_dimensions = {
                    str(ref)
                    for item in closure_evidence
                    for ref in (item.get("fidelity_dimensions") or [])
                }
                if observed_dimensions != set(semantic_contract["fidelity_dimensions"]):
                    raise ValueError("closure_fidelity_dimensions_incomplete")
                required_obligations = set(semantic_contract["obligation_refs"])
                required_oracles = set(semantic_contract["oracle_refs"])
                required_dimensions = set(semantic_contract["fidelity_dimensions"])
                if any(
                    not isinstance(item.get("obligation_refs"), list)
                    or not set(item.get("obligation_refs") or [])
                    or not set(item.get("obligation_refs") or []).issubset(
                        required_obligations
                    )
                    or not isinstance(item.get("oracle_refs"), list)
                    or not set(item.get("oracle_refs") or [])
                    or not set(item.get("oracle_refs") or []).issubset(required_oracles)
                    or not isinstance(item.get("fidelity_dimensions"), list)
                    or not set(item.get("fidelity_dimensions") or [])
                    or not set(item.get("fidelity_dimensions") or []).issubset(
                        required_dimensions
                    )
                    or item.get("source_digest") != semantic_contract["source_digest"]
                    or item.get("spec_digest") != semantic_contract["spec_digest"]
                    for item in closure_evidence
                ):
                    raise ValueError("closure_semantic_evidence_invalid")
                if any(
                    not _claim_evidence_contract_matches(item, claim_contracts)
                    for item in closure_evidence
                ):
                    raise ValueError("closure_evidence_claim_contract_mismatch")
                if any(
                    type(item.get("claim_id")) is not str
                    or not str(item.get("claim_id") or "").strip()
                    or item.get("claim_id") not in required
                    for item in closure_evidence
                ):
                    raise ValueError("closure_evidence_claim_binding_invalid")
                if {
                    item.get("claim_id") for item in closure_evidence
                } != required:
                    raise ValueError("closure_evidence_claim_binding_incomplete")
            if not closure_verification or any(
                not str(item.get("command") or "").strip()
                or not isinstance(item.get("exit_code"), int)
                or isinstance(item.get("exit_code"), bool)
                or int(item["exit_code"]) != 0
                or item.get("candidate_digest") != candidate
                or bool(set(item) - {"command", "exit_code", "candidate_digest"})
                for item in closure_verification
            ):
                raise ValueError("closure_verification_failed")

            evidence_graph = _decode(task["evidence_graph_json"], [])
            candidate_evidence = [
                item
                for item in evidence_graph
                if item.get("candidate_digest") == candidate
            ]
            candidate_claim_items = [
                claim
                for item in candidate_evidence
                for claim in (item.get("claims") or [])
            ]
            if any(not _native_identity(claim) for claim in candidate_claim_items):
                raise ValueError("candidate_evidence_claim_identity_invalid")
            candidate_claims = set(candidate_claim_items)
            if not required.issubset(candidate_claims):
                raise ValueError("candidate_evidence_claims_incomplete")

            def without_candidate(item: Mapping[str, Any]) -> dict[str, Any]:
                return {
                    str(key): value
                    for key, value in item.items()
                    if str(key) != "candidate_digest"
                }

            admitted_refs = {
                _json(dict(ref))
                for item in candidate_evidence
                for ref in (item.get("evidence_refs") or [])
                if isinstance(ref, Mapping)
            }
            if any(
                _json(dict(item) if semantic_contract else without_candidate(item))
                not in admitted_refs
                for item in closure_evidence
            ):
                raise ValueError("closure_evidence_not_admitted")

            admitted_verification = {
                _json(dict(result))
                for item in candidate_evidence
                for result in (item.get("verification_results") or [])
                if isinstance(result, Mapping)
            }
            if any(
                _json(without_candidate(item)) not in admitted_verification
                for item in closure_verification
            ):
                raise ValueError("closure_verification_not_admitted")
            allowed_commands = {
                str(command)
                for row in conn.execute(
                    "SELECT definition_json FROM task_slices WHERE task_id = ?",
                    (task_id,),
                ).fetchall()
                for command in (
                    _decode(row["definition_json"], {}).get("verification_commands") or []
                )
            }
            if any(
                str(item.get("command") or "") not in allowed_commands
                for item in closure_verification
            ):
                raise ValueError("closure_verification_command_not_frozen")
            closure = {
                "schema": "hermes_closure_receipt_v1",
                "task_id": task_id,
                "candidate_digest": candidate_digest,
                "verifier_session_id": verifier,
                "closure_packet_id": str(packet["packet_id"]),
                "closure_packet_digest": packet_digest,
                "claims": dict(claims),
                "evidence_refs": closure_evidence,
                "verification_results": closure_verification,
                "closed_at": _utc_now(),
            }
            closure.update(
                {
                    key: semantic_contract[key]
                    for key in (
                        "source_digest",
                        "spec_digest",
                        "obligation_digest",
                        "oracle_refs",
                        "fidelity_dimensions",
                    )
                    if key in semantic_contract
                }
            )
            conn.execute(
                "UPDATE tasks SET status = 'closed', closure_json = ?, state_revision = state_revision + 1, updated_at = ? WHERE task_id = ?",
                (_json(closure), _utc_now(), task_id),
            )
            self._record_state_event(
                conn,
                task_id,
                int(task["state_revision"]) + 1,
                "task_closed",
                {"candidate_digest": candidate_digest, "verifier_session_id": verifier},
            )
        return self.get_task(task_id)

    @staticmethod
    def _projection_digest(conn: sqlite3.Connection, task_id: str) -> str:
        """Digest the authoritative materialized task projection at an event boundary."""
        task = conn.execute(
            "SELECT schema_name, owner_profile, title, status, original_sources_json, "
            "source_events_json, spec_bundle_json, implementation_graph_json, "
            "evidence_graph_json, closure_json, spec_revision, plan_revision, "
            "state_revision, candidate_digest, created_at, updated_at "
            "FROM tasks WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if task is None:
            raise KeyError(task_id)

        def rows(query: str) -> list[dict[str, Any]]:
            return [dict(row) for row in conn.execute(query, (task_id,)).fetchall()]

        spec_reviews = rows(
            "SELECT review_id, spec_digest, source_digest, reviewer_session_id, "
            "verdict, findings_json, recorded_at FROM spec_reviews "
            "WHERE task_id = ? ORDER BY recorded_at, review_id"
        )
        projection = {
            "task": dict(task),
            "source_events": rows(
                "SELECT event_id, payload_json, recorded_at FROM source_events "
                "WHERE task_id = ? ORDER BY rowid"
            ),
            "bindings": rows(
                "SELECT entrypoint, session_id, created_at FROM entrypoint_bindings "
                "WHERE task_id = ? ORDER BY entrypoint, session_id"
            ),
            "slices": rows(
                "SELECT slice_id, ordinal, definition_json, status, lease_owner, "
                "lease_token, lease_expires_at, result_id, integrated_state_revision "
                "FROM task_slices WHERE task_id = ? ORDER BY ordinal, slice_id"
            ),
            "packets": rows(
                "SELECT packet_id, slice_id, spec_revision, plan_revision, state_revision, "
                "lease_owner, lease_token, lease_expires_at, packet_digest, "
                "payload_json, status, created_at FROM context_packets "
                "WHERE task_id = ? ORDER BY packet_id"
            ),
            "results": rows(
                "SELECT result_id, slice_id, packet_id, worker_session_id, "
                "payload_digest, payload_json, disposition, reasons_json, "
                "integrator_session_id, integrated_state_revision, created_at, updated_at "
                "FROM worker_results WHERE task_id = ? ORDER BY result_id"
            ),
        }
        # Preserve projection-digest compatibility for pre-review databases: the
        # historical projection had no spec_reviews key. The key becomes authoritative
        # only once the first immutable review exists.
        if spec_reviews:
            projection["spec_reviews"] = spec_reviews
        return _digest(projection)

    @staticmethod
    def _state_event_ledger_valid(
        task_id: str,
        current_revision: int,
        rows: Sequence[sqlite3.Row],
    ) -> bool:
        if not rows or int(rows[-1]["state_revision"]) != int(current_revision):
            return False
        first_revision = int(rows[0]["state_revision"])
        if [int(row["state_revision"]) for row in rows] != list(
            range(first_revision, int(current_revision) + 1)
        ):
            return False
        previous_digest = ""
        for row in rows:
            if str(row["previous_event_digest"]) != previous_digest:
                return False
            identity = {
                "task_id": task_id,
                "state_revision": int(row["state_revision"]),
                "event_type": str(row["event_type"]),
                "payload": _decode(row["payload_json"], {}),
                "previous_event_digest": previous_digest,
                "recorded_at": str(row["recorded_at"]),
            }
            if _digest(identity) != str(row["event_digest"]):
                return False
            previous_digest = str(row["event_digest"])
        return True

    @staticmethod
    def _record_state_event(
        conn: sqlite3.Connection,
        task_id: str,
        state_revision: int,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        previous = conn.execute(
            "SELECT event_digest FROM task_state_events WHERE task_id = ? "
            "ORDER BY state_revision DESC LIMIT 1",
            (task_id,),
        ).fetchone()
        previous_digest = str(previous["event_digest"] if previous else "")
        recorded_at = _utc_now()
        event_payload = dict(payload)
        event_payload["_projection_digest"] = TaskRuntimeStore._projection_digest(
            conn, task_id
        )
        identity = {
            "task_id": task_id,
            "state_revision": int(state_revision),
            "event_type": str(event_type),
            "payload": event_payload,
            "previous_event_digest": previous_digest,
            "recorded_at": recorded_at,
        }
        event_digest = _digest(identity)
        conn.execute(
            "INSERT INTO task_state_events "
            "(task_id, state_revision, event_type, payload_json, previous_event_digest, "
            "event_digest, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                task_id,
                int(state_revision),
                str(event_type),
                _json(event_payload),
                previous_digest,
                event_digest,
                recorded_at,
            ),
        )

    def _assert_owner(self, row: sqlite3.Row) -> None:
        if str(row["owner_profile"]) != self.owner_profile:
            raise PermissionError("task_owner_profile_mismatch")

    def _task_row(self, conn: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        self._assert_owner(row)
        state_events = conn.execute(
            "SELECT state_revision, event_type, payload_json, previous_event_digest, "
            "event_digest, recorded_at FROM task_state_events WHERE task_id = ? "
            "ORDER BY state_revision",
            (task_id,),
        ).fetchall()
        if not self._state_event_ledger_valid(
            task_id, int(row["state_revision"]), state_events
        ):
            raise ValueError("task_state_event_ledger_invalid")
        tip_payload = _decode(state_events[-1]["payload_json"], {})
        if str(tip_payload.get("_projection_digest") or "") != self._projection_digest(
            conn, task_id
        ):
            raise ValueError("task_projection_digest_invalid")
        return row

    @staticmethod
    def _expect_revision(row: sqlite3.Row, expected: int) -> None:
        actual = int(row["state_revision"])
        if actual != int(expected):
            raise RevisionConflict(f"state_revision_conflict expected={expected} actual={actual}")

    @staticmethod
    def _bump_state(
        conn: sqlite3.Connection,
        task_id: str,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> int:
        row = conn.execute(
            "SELECT state_revision FROM tasks WHERE task_id = ?", (task_id,)
        ).fetchone()
        revision = int(row["state_revision"]) + 1
        conn.execute(
            "UPDATE tasks SET state_revision = ?, updated_at = ? WHERE task_id = ?",
            (revision, _utc_now(), task_id),
        )
        TaskRuntimeStore._record_state_event(
            conn, task_id, revision, event_type, payload
        )
        return revision

    @staticmethod
    def _invalidate_claims(conn: sqlite3.Connection, task_id: str, reason: str) -> None:
        invalidation_reason = f"invalidated_by_{str(reason or 'revision_change')}"
        conn.execute(
            "UPDATE context_packets SET status = 'invalidated' "
            "WHERE task_id = ? AND status IN ('active', 'submitted')",
            (task_id,),
        )
        conn.execute(
            "UPDATE worker_results SET disposition = 'invalidated', reasons_json = ?, updated_at = ? "
            "WHERE task_id = ? AND disposition IN ('accepted_for_integration', 'integrated')",
            (_json([invalidation_reason]), _utc_now(), task_id),
        )
        conn.execute(
            "UPDATE task_slices SET status = 'pending', lease_owner = '', lease_token = '', "
            "lease_expires_at = NULL, result_id = '', integrated_state_revision = NULL "
            "WHERE task_id = ? AND status IN ('claimed', 'submitted', 'completed')",
            (task_id,),
        )

    @staticmethod
    def _expire_leases(conn: sqlite3.Connection, task_id: str, now: float) -> list[str]:
        expired = conn.execute(
            "SELECT slice_id FROM task_slices WHERE task_id = ? AND status = 'claimed' AND lease_expires_at < ?",
            (task_id, now),
        ).fetchall()
        expired_slice_ids = [str(row["slice_id"]) for row in expired]
        for row in expired:
            conn.execute(
                "UPDATE task_slices SET status = 'quarantined', lease_expires_at = NULL "
                "WHERE task_id = ? AND slice_id = ?",
                (task_id, row["slice_id"]),
            )
            conn.execute(
                "UPDATE context_packets SET status = 'quarantined' "
                "WHERE task_id = ? AND slice_id = ? AND status = 'active'",
                (task_id, row["slice_id"]),
            )
        if expired_slice_ids:
            TaskRuntimeStore._bump_state(
                conn,
                task_id,
                "lease_expired_quarantined",
                {
                    "expired_slice_ids": expired_slice_ids,
                    "termination_proven": False,
                },
            )
        return expired_slice_ids

    @staticmethod
    def _validate_plan(definitions: Sequence[Mapping[str, Any]]) -> None:
        if not definitions:
            raise ValueError("implementation_graph_required")
        ids: list[str] = []
        for definition in definitions:
            slice_id = definition.get("slice_id")
            if not _native_identity(slice_id):
                raise ValueError("slice_ids_must_be_unique_and_nonempty")
            assert type(slice_id) is str
            ids.append(slice_id)
        if len(set(ids)) != len(ids):
            raise ValueError("slice_ids_must_be_unique_and_nonempty")
        known = set(ids)
        for item in definitions:
            dependency_items = item.get("dependencies") or []
            if not isinstance(dependency_items, list) or any(
                not _native_identity(dep) for dep in dependency_items
            ):
                raise ValueError("slice_dependency_identity_invalid")
            dependencies = set(dependency_items)
            if not dependencies.issubset(known):
                raise ValueError("slice_dependency_missing")
            if item["slice_id"] in dependencies:
                raise ValueError("slice_self_dependency")
            required = {
                "objective", "required_clauses", "negative_requirements", "source_refs",
                "owned_paths", "forbidden_changes", "verification_commands", "authority",
            }
            if any(field not in item for field in required):
                raise ValueError("slice_definition_missing_required_fields")
        visited: set[str] = set()
        visiting: set[str] = set()
        by_id = {item["slice_id"]: item for item in definitions}

        def visit(slice_id: str) -> None:
            if slice_id in visiting:
                raise ValueError("implementation_graph_cycle")
            if slice_id in visited:
                return
            visiting.add(slice_id)
            for dependency in by_id[slice_id].get("dependencies") or []:
                visit(str(dependency))
            visiting.remove(slice_id)
            visited.add(slice_id)

        for slice_id in ids:
            visit(slice_id)

    @staticmethod
    def _result_payload(row: sqlite3.Row, *, idempotent_replay: bool) -> dict[str, Any]:
        return {
            "schema": WORKER_RESULT_SCHEMA,
            "result_id": row["result_id"],
            "task_id": row["task_id"],
            "slice_id": row["slice_id"],
            "packet_id": row["packet_id"],
            "worker_session_id": row["worker_session_id"],
            "payload_digest": row["payload_digest"],
            "disposition": row["disposition"],
            "reasons": _decode(row["reasons_json"], []),
            "idempotent_replay": idempotent_replay,
        }
