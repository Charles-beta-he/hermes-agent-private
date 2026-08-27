"""Durable task-centric multi-session control plane.

The store is deliberately provider- and entrypoint-neutral.  CLI, Telegram,
delegated workers, and closure sessions exchange immutable packets through one
SQLite SSOT; natural-language conversation summaries are never authoritative.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import os
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


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _decode(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


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
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
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
            # One-time compatibility backfill from the legacy mutable JSON snapshot.
            # The normalized table is authoritative after this point; INSERT OR IGNORE
            # preserves exact replay semantics if an older database is reopened.
            rows = conn.execute(
                "SELECT task_id, source_events_json FROM tasks WHERE source_events_json != '[]'"
            ).fetchall()
            for row in rows:
                for event in _decode(row["source_events_json"], []):
                    if not isinstance(event, dict):
                        continue
                    event_id = str(event.get("event_id") or _digest(event))
                    payload = dict(event)
                    payload["event_id"] = event_id
                    recorded_at = str(payload.get("recorded_at") or _utc_now())
                    payload["recorded_at"] = recorded_at
                    conn.execute(
                        "INSERT OR IGNORE INTO source_events "
                        "(task_id, event_id, payload_json, recorded_at) VALUES (?, ?, ?, ?)",
                        (row["task_id"], event_id, _json(payload), recorded_at),
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
        if not sources:
            raise ValueError("original_sources_required")
        for item in sources:
            if not str(item.get("source_id") or "") or not str(item.get("content") or ""):
                raise ValueError("original_source_missing_identity_or_content")
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
                "ORDER BY recorded_at, event_id",
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
                "ORDER BY recorded_at, event_id",
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
            str(item.get("source_id") or "")
            for item in source_snapshot.get("original_sources") or []
            if isinstance(item, Mapping)
        ]
        identifiers.extend(
            str(item.get("event_id") or "")
            for item in source_snapshot.get("source_events") or []
            if isinstance(item, Mapping)
        )
        if any(not item for item in identifiers) or len(identifiers) != len(set(identifiers)):
            raise ValueError("task_source_identity_invalid")
        return identifiers

    @staticmethod
    def _validate_governed_spec(
        spec: Mapping[str, Any], *, source_ids: Sequence[str]
    ) -> None:
        governance = spec.get("governance")
        if not isinstance(governance, Mapping):
            raise ValueError("spec_governance_required")
        task_class = str(governance.get("task_class") or "")
        if task_class not in TASK_CLASSES:
            raise ValueError("spec_task_class_invalid")

        known_sources = set(source_ids)
        source_authority = [str(item) for item in governance.get("source_authority") or []]
        if (
            not source_authority
            or len(source_authority) != len(set(source_authority))
            or set(source_authority) != known_sources
        ):
            raise ValueError("spec_source_authority_incomplete")
        oracle_refs = {str(item) for item in governance.get("oracle_refs") or []}
        if not oracle_refs or not oracle_refs.issubset(known_sources):
            raise ValueError("spec_oracle_refs_invalid")
        source_coverage = governance.get("source_coverage")
        if not isinstance(source_coverage, Mapping) or set(source_coverage) != known_sources:
            raise ValueError("spec_source_coverage_incomplete")
        if any(
            not isinstance(source_coverage[source_id], list)
            or not source_coverage[source_id]
            or any(not str(item or "").strip() for item in source_coverage[source_id])
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
                claim_id = str(claim.get("id") or "")
                refs = {str(item) for item in claim.get("source_refs") or []}
                if not claim_id or claim_id in claim_ids:
                    raise ValueError("spec_claim_identity_invalid")
                if not refs or not refs.issubset(known_sources):
                    raise ValueError("spec_claim_source_refs_invalid")
                claim_ids.add(claim_id)
                claim_source_edges.update((source_id, claim_id) for source_id in refs)
        covered_claim_ids = {
            str(claim_id)
            for source_id in known_sources
            for claim_id in source_coverage[source_id]
        }
        if covered_claim_ids != claim_ids:
            raise ValueError("spec_source_coverage_incomplete")
        coverage_edges = {
            (source_id, str(claim_id))
            for source_id in known_sources
            for claim_id in source_coverage[source_id]
        }
        if coverage_edges != claim_source_edges:
            raise ValueError("spec_source_coverage_edge_mismatch")
        if set(str(item) for item in spec.get("closure_criteria") or []) != claim_ids:
            raise ValueError("spec_closure_coverage_incomplete")

        discovery = governance.get("discovery")
        if not isinstance(discovery, Mapping) or not DISCOVERY_LANES.issubset(discovery):
            raise ValueError("spec_discovery_preflight_incomplete")
        for lane_name in DISCOVERY_LANES:
            lane = discovery[lane_name]
            if not isinstance(lane, Mapping):
                raise ValueError("spec_discovery_preflight_invalid")
            status = str(lane.get("status") or "")
            refs = {str(item) for item in lane.get("source_refs") or []}
            if status not in PREFLIGHT_STATUSES:
                raise ValueError("spec_discovery_preflight_invalid")
            if not refs or not refs.issubset(known_sources):
                raise ValueError("spec_discovery_source_refs_invalid")
            if not str(lane.get("decision") or "").strip():
                raise ValueError("spec_discovery_decision_required")
        if str(discovery["project_evidence"].get("status")) != "completed":
            raise ValueError("project_evidence_preflight_required")
        required_lane = {
            "mature_capability": "open_source",
            "product_design": "competitor",
            "reference_reimplementation": "prototype",
        }.get(task_class)
        if required_lane and str(discovery[required_lane].get("status")) != "completed":
            raise ValueError(f"{required_lane}_preflight_required_for_task_class")
        if task_class == "reference_reimplementation":
            prototype_refs = {
                str(item) for item in discovery["prototype"].get("source_refs") or []
            }
            if not prototype_refs or not prototype_refs.issubset(oracle_refs):
                raise ValueError("prototype_oracle_binding_required")
        if str(discovery["open_source"].get("status")) == "completed":
            evaluation = discovery["open_source"].get("evaluation")
            if (
                not isinstance(evaluation, Mapping)
                or not OPEN_SOURCE_EVALUATION_DIMENSIONS.issubset(evaluation)
                or any(
                    not str(evaluation[dimension]).strip()
                    for dimension in OPEN_SOURCE_EVALUATION_DIMENSIONS
                )
            ):
                raise ValueError("open_source_evaluation_incomplete")
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
                self._validate_governed_spec(spec, source_ids=source_ids)
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
        if not payload.get("content"):
            raise ValueError("source_event_content_required")
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            event_id = str(payload.get("event_id") or _digest(payload))
            existing_row = conn.execute(
                "SELECT payload_json FROM source_events WHERE task_id = ? AND event_id = ?",
                (task_id, event_id),
            ).fetchone()
            if existing_row is not None:
                existing_identity = _decode(existing_row["payload_json"], {})
                existing_identity.pop("recorded_at", None)
                replay_identity = dict(payload)
                replay_identity["event_id"] = event_id
                replay_identity.pop("recorded_at", None)
                if existing_identity != replay_identity:
                    raise ValueError("source_event_id_conflict")
            else:
                payload["event_id"] = event_id
                payload.setdefault("recorded_at", _utc_now())
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
            self._validate_governed_spec(spec, source_ids=source_ids)
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
            self._validate_governed_spec(spec, source_ids=source_ids)
            approved_review = conn.execute(
                "SELECT 1 FROM spec_reviews WHERE task_id = ? AND spec_digest = ? "
                "AND source_digest = ? AND verdict = 'approved' LIMIT 1",
                (task_id, _digest(spec), _digest(source_snapshot)),
            ).fetchone()
            if approved_review is None:
                raise ValueError("independent_spec_review_required")
            spec_claims = {
                str(item.get("id") or "")
                for field in ("required_clauses", "negative_requirements")
                for item in (spec.get(field) or [])
                if isinstance(item, Mapping) and str(item.get("id") or "")
            }
            closure_criteria = {
                str(item) for item in spec.get("closure_criteria") or []
            }
            assigned_claims = {
                str(claim)
                for definition in definitions
                for field in ("required_clauses", "negative_requirements")
                for claim in (definition.get(field) or [])
            }
            if not assigned_claims.issubset(spec_claims):
                raise ValueError("slice_claim_not_in_frozen_spec")
            if not closure_criteria.issubset(assigned_claims):
                raise ValueError("plan_closure_criteria_unassigned")
            source_ids = {
                str(item.get("source_id") or "")
                for item in _decode(row["original_sources_json"], [])
                if isinstance(item, Mapping) and str(item.get("source_id") or "")
            }
            source_ids.update(
                str(event["event_id"])
                for event in conn.execute(
                    "SELECT event_id FROM source_events WHERE task_id = ?", (task_id,)
                ).fetchall()
            )
            for definition in definitions:
                refs = {str(item) for item in definition.get("source_refs") or []}
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
                        not isinstance(item, str) or not item
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
                    "slice_ids": [str(item["slice_id"]) for item in definitions],
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
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            if str(task["status"]) in {"draft", "closed"}:
                raise ValueError("task_not_claimable")
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
        payload = dict(packet)
        expected = str(payload.pop("packet_digest", ""))
        return bool(expected) and expected == _digest(payload)

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
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
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
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
        if not isinstance(termination_proof, Mapping):
            raise ValueError("termination_proof_required")
        proof = dict(termination_proof)
        allowed_proof_fields = {
            "kind", "worker_session_id", "packet_id", "lease_token", "observed_at"
        }
        if set(proof) - allowed_proof_fields:
            raise ValueError("termination_proof_unknown_fields")
        if str(proof.get("kind") or "").strip() != "runner_not_started":
            raise ValueError("termination_proof_kind_invalid")
        if str(proof.get("worker_session_id") or "").strip() != worker:
            raise ValueError("termination_proof_worker_mismatch")
        if str(proof.get("packet_id") or "").strip() != str(packet_id):
            raise ValueError("termination_proof_packet_mismatch")
        observed_at = str(proof.get("observed_at") or "").strip()
        if not observed_at:
            raise ValueError("termination_proof_observed_at_required")
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
        worker = str(worker_session_id or "").strip()
        if not worker:
            raise ValueError("worker_session_id_required")
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
        payload = dict(result)
        worker = str(worker_session_id or "")
        now_value = float(time.time() if now is None else now)
        identity = _result_id(packet_id, worker, payload)
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            existing = conn.execute("SELECT * FROM worker_results WHERE result_id = ?", (identity,)).fetchone()
            if existing:
                return self._result_payload(existing, idempotent_replay=True)
            packet = conn.execute("SELECT * FROM context_packets WHERE packet_id = ?", (packet_id,)).fetchone()
            reasons: list[str] = []
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
                required_fields = {
                    "claims", "changed_artifacts", "evidence_refs", "verification_results",
                    "risks", "unknowns", "state_delta_proposal", "candidate_digest",
                }
                allowed_fields = required_fields | {"slice_id", "input_digest"}
                if set(payload) - allowed_fields:
                    reasons.append("worker_result_unknown_fields")
                if any(field not in payload for field in required_fields):
                    reasons.append("worker_result_missing_required_fields")
                else:
                    list_fields = (
                        "claims",
                        "changed_artifacts",
                        "evidence_refs",
                        "verification_results",
                        "risks",
                        "unknowns",
                    )
                    if any(not isinstance(payload.get(field), list) for field in list_fields):
                        reasons.append("worker_result_invalid_field_types")
                    else:
                        claims = {str(item) for item in payload["claims"] if str(item)}
                        assigned_claims = {
                            str(item)
                            for field in ("required_clauses", "negative_requirements")
                            for item in (packet_payload.get(field) or [])
                        }
                        if not assigned_claims.issubset(claims):
                            reasons.append("worker_claims_incomplete")
                        evidence_refs = payload["evidence_refs"]
                        if not evidence_refs or any(
                            not isinstance(item, Mapping)
                            or not str(item.get("ref") or "").strip()
                            for item in evidence_refs
                        ):
                            reasons.append("worker_evidence_refs_invalid")
                        elif any(
                            set(item) - {"kind", "ref"}
                            for item in evidence_refs
                            if isinstance(item, Mapping)
                        ):
                            reasons.append("worker_evidence_refs_unknown_fields")
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
        integrator = str(integrator_session_id or "").strip()
        if not integrator:
            raise ValueError("integrator_session_id_required")
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
        verifier = str(verifier_session_id or "").strip()
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
                    "SELECT payload_json FROM source_events WHERE task_id = ? ORDER BY recorded_at, event_id",
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
        verifier = str(verifier_session_id or "").strip()
        with self._write() as conn:
            task = self._task_row(conn, task_id)
            if (
                str(task["status"]) == "closed"
                and str(candidate_digest) != str(task["candidate_digest"])
            ):
                raise ValueError("candidate_digest_mismatch")
            packet = dict(closure_packet or {})
            packet_digest = str(packet.pop("packet_digest", "") or "")
            if not packet_digest or packet_digest != _digest(packet):
                raise ValueError("closure_packet_digest_invalid")
            if str(task["status"]) == "closed":
                existing = _decode(task["closure_json"], {})
                replay_matches = (
                    str(existing.get("candidate_digest") or "") == str(candidate_digest)
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
                    "SELECT payload_json FROM source_events WHERE task_id = ? ORDER BY recorded_at, event_id",
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
            if any(packet.get(key) != value for key, value in expected_packet_fields.items()):
                raise ValueError("closure_packet_stale")
            if str(candidate_digest) != str(task["candidate_digest"]):
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
            passed = {str(key) for key, value in claims.items() if str(value).lower() == "pass"}
            if not required.issubset(passed):
                raise ValueError("closure_claims_incomplete")
            closure_evidence = [dict(item) for item in evidence_refs]
            closure_verification = [dict(item) for item in verification_results]
            candidate = str(candidate_digest)
            if not closure_evidence or any(
                not str(item.get("ref") or "").strip()
                or str(item.get("candidate_digest") or "") != candidate
                or bool(set(item) - {"kind", "ref", "candidate_digest"})
                for item in closure_evidence
            ):
                raise ValueError("closure_evidence_required")
            if not closure_verification or any(
                not str(item.get("command") or "").strip()
                or not isinstance(item.get("exit_code"), int)
                or isinstance(item.get("exit_code"), bool)
                or int(item["exit_code"]) != 0
                or str(item.get("candidate_digest") or "") != candidate
                or bool(set(item) - {"command", "exit_code", "candidate_digest"})
                for item in closure_verification
            ):
                raise ValueError("closure_verification_failed")

            evidence_graph = _decode(task["evidence_graph_json"], [])
            candidate_evidence = [
                item
                for item in evidence_graph
                if str(item.get("candidate_digest") or "") == candidate
            ]
            candidate_claims = {
                str(claim)
                for item in candidate_evidence
                for claim in (item.get("claims") or [])
                if str(claim)
            }
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
                _json(without_candidate(item)) not in admitted_refs
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
                "WHERE task_id = ? ORDER BY recorded_at, event_id"
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
        ids = [str(item.get("slice_id") or "") for item in definitions]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("slice_ids_must_be_unique_and_nonempty")
        known = set(ids)
        for item in definitions:
            dependencies = {str(dep) for dep in item.get("dependencies") or []}
            if not dependencies.issubset(known):
                raise ValueError("slice_dependency_missing")
            if str(item["slice_id"]) in dependencies:
                raise ValueError("slice_self_dependency")
            required = {
                "objective", "required_clauses", "negative_requirements", "source_refs",
                "owned_paths", "forbidden_changes", "verification_commands", "authority",
            }
            if any(field not in item for field in required):
                raise ValueError("slice_definition_missing_required_fields")
        visited: set[str] = set()
        visiting: set[str] = set()
        by_id = {str(item["slice_id"]): item for item in definitions}

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
