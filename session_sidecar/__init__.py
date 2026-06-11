"""
Session Sidecar — structured agent-run observation layer.

Produces lightweight JSON sidecar files at the end of every agent session
spanning CLI, cron, automation (browser), and script pathways. Consumers
include daily reports, taskpool evidence ingestion, and selfcheck.

Architecture:

    Entrypoint (CLI, cron, browser, script)
        |
        v
    session_sidecar.producer  -- builds a SessionSidecar dataclass
        |
        v
    session_sidecar.writer    -- serialises to ~/.hermes/sidecars/YYYY-MM-DD/run_id.json
        |                       + appends to ~/.hermes/sidecars/YYYY-MM-DD/daily.ndjson
        v
    session_sidecar.reader    -- consumer API (query by run_id, date range, entrypoint)
        |
        v
    Consumer (taskpool proof, daily report, selfcheck)

Redaction:
    Every sidecar carries a ``redaction_boundary`` declaring which fields are
    production-safe. The ``strip_sensitive()`` helper removes fields carrying
    PII, secrets, or credentials before cross-profile / external sharing.
"""

from __future__ import annotations

import json
import os
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

try:
    from hermes_constants import get_hermes_home
except (ImportError, TypeError):
    # Local taskpool scripts still run under the macOS system Python (3.9),
    # while hermes_constants may use newer annotation syntax.  Keep the
    # sidecar observation layer importable for selfcheck/bridge probes without
    # widening the change to global Hermes constants.
    def get_hermes_home() -> Path:
        return Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema version — bump on structural change; fixtures and consumers MUST
# check this before assuming field presence.
# ---------------------------------------------------------------------------
SIDECAR_SCHEMA_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Well-known entrypoint labels
# ---------------------------------------------------------------------------
ENTRYPOINT_CLI = "cli"
ENTRYPOINT_CRON = "cron"
ENTRYPOINT_BROWSER = "automation"
ENTRYPOINT_SCRIPT = "script"
ENTRYPOINT_GATEWAY = "gateway"
ENTRYPOINT_KANBAN_WORKER = "kanban_worker"
ENTRYPOINT_ACP = "acp_adapter"
_VALID_ENTRYPOINTS: Set[str] = {
    ENTRYPOINT_CLI, ENTRYPOINT_CRON, ENTRYPOINT_BROWSER,
    ENTRYPOINT_SCRIPT, ENTRYPOINT_GATEWAY,
    ENTRYPOINT_KANBAN_WORKER, ENTRYPOINT_ACP,
}

# ---------------------------------------------------------------------------
# Sensitive‑field markers for redaction
# ---------------------------------------------------------------------------
_REDACTION_PII = "pii"
_REDACTION_SECRET = "secret"
_REDACTION_CREDENTIAL = "credential"
_REDACTION_TOKEN = "token"
_REDACTION_SYS_PATH = "system_path"
_REDACTION_DEFAULT: Set[str] = {
    _REDACTION_PII, _REDACTION_SECRET, _REDACTION_CREDENTIAL, _REDACTION_TOKEN,
}

# ---------------------------------------------------------------------------
# Redacted sentinel — replaces the real value.
# ---------------------------------------------------------------------------
_REDACTED_SENTINEL = "**REDACTED**"


# ---------------------------------------------------------------------------
# Schema dataclass
# ---------------------------------------------------------------------------
@dataclass
class ArtifactRef:
    """A file or URI produced during the session."""
    path: str                         # absolute path or URI
    kind: str = "file"                # file | screenshot | log | json
    sha256: str = ""                  # content hash when available
    size_bytes: int = 0
    description: str = ""
    redaction_cause: str = ""         # non‑empty → sensitive, stripped on export


@dataclass
class SessionSidecar:
    """
    Structured summary of a single agent‑run session.

    Core contract fields (always present in every sidecar):
        schema_version, run_id, entrypoint, created_at, duration_s,
        status (succeeded | degraded | failed)

    Evidence fields (populated when the session produced observable output):
        artifacts, error_class, degraded_flags, evidence_refs, model, provider

    Redaction boundary:
        redacted_fields     – fields whose values were replaced by the sentinel
        redaction_catalog   – mapping of paths → cause for post‑hoc audit
        redaction_default   – set of causes redacted by default in strip_sensitive()
    """
    # -- identity
    schema_version: str = SIDECAR_SCHEMA_VERSION
    run_id: str = ""
    entrypoint: str = ""
    session_id: str = ""
    task_id: str = ""

    # -- timing
    created_at: str = ""              # ISO‑8601
    duration_s: float = 0.0

    # -- outcome
    status: str = "succeeded"         # succeeded | degraded | failed
    error_class: str = ""             # e.g. "TimeoutError", "RateLimitError"
    error_message: str = ""           # short summary (no full traceback)
    degraded_flags: List[str] = field(default_factory=list)

    # -- provenance
    model: str = ""
    provider: str = ""
    platform: str = ""

    # -- artifacts & evidence
    artifacts: List[ArtifactRef] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)   # "proof:...", "artifact:..."

    # -- redaction boundary
    redacted_fields: List[str] = field(default_factory=list)
    redaction_catalog: Dict[str, str] = field(default_factory=dict)  # field_path → cause
    redaction_default: Set[str] = field(default_factory=lambda: set(_REDACTION_DEFAULT))

    # -- optional custom metadata
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- lifecycle (for taskpool integration)
    lifecycle_transitions: List[Dict[str, str]] = field(default_factory=list)

    def __post_init__(self):
        if not self.run_id:
            self.run_id = _generate_run_id()
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if self.entrypoint and self.entrypoint not in _VALID_ENTRYPOINTS:
            logger.warning("Unknown entrypoint %r; valid: %s",
                           self.entrypoint, sorted(_VALID_ENTRYPOINTS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _generate_run_id() -> str:
    """Short unique run identifier: timestamp + hostname + random hex."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    rand = os.urandom(4).hex()
    host = os.uname().nodename.partition(".")[0]
    return f"{ts}-{host}-{rand}"


def _ensure_sidecar_dir(date_prefix: str) -> Path:
    """Return the date‑partitioned sidecar directory, creating it if needed."""
    d = get_hermes_home() / "sidecars" / date_prefix
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
def sidecar_to_dict(sc: SessionSidecar, *, strip_sensitive: bool = True) -> Dict[str, Any]:
    """Convert a SessionSidecar to a JSON‑safe dict, optionally redacting.

    When *strip_sensitive* is True (the default), fields whose
    ``redaction_cause`` falls in ``redaction_default`` are replaced with the
    sentinel.  The sentinel entries are listed in ``redacted_fields`` and
    the original causes are preserved in ``redaction_catalog`` so audits
    can still determine *why* something was hidden.
    """
    d = asdict(sc)
    if not strip_sensitive:
        # Convert redaction_default from set to list for JSON
        d["redaction_default"] = list(d.get("redaction_default", set()))
        return d

    redacted: List[str] = []
    catalog: Dict[str, str] = {}
    causes = set(sc.redaction_default) if sc.redaction_default else set(_REDACTION_DEFAULT)

    # Redact artifact paths by cause
    cleaned_artifacts: List[Dict[str, Any]] = []
    for a in d.get("artifacts", []):
        if a.get("redaction_cause", "") in causes:
            path_key = a["path"]
            catalog[path_key] = a["redaction_cause"]
            a["path"] = _REDACTED_SENTINEL
            a["sha256"] = _REDACTED_SENTINEL
            redacted.append(path_key)
        # Strip internal field before serialising
        a.pop("redaction_cause", None)
        cleaned_artifacts.append(a)
    d["artifacts"] = cleaned_artifacts

    # Redact evidence_refs that look sensitive
    cleaned_refs: List[str] = []
    for ref in d.get("evidence_refs", []):
        if any(
            marker in ref.lower()
            for marker in ("token", "secret", "key=", "password", "credential")
        ):
            catalog[ref] = _REDACTION_CREDENTIAL
            cleaned_refs.append(_REDACTED_SENTINEL)
            redacted.append(ref)
        else:
            cleaned_refs.append(ref)
    d["evidence_refs"] = cleaned_refs

    # Redact error_message that might contain PII
    em = d.get("error_message", "")
    if em and any(
        marker in em.lower()
        for marker in ("token", "authorization", "api-key", "secret")
    ):
        catalog["error_message"] = _REDACTION_SECRET
        d["error_message"] = _REDACTED_SENTINEL
        redacted.append("error_message")

    # Redact extra fields by cause in redaction_catalog
    extra = d.get("extra", {})
    if isinstance(extra, dict):
        for field_path, cause in list(catalog.items()):
            if field_path.startswith("extra."):
                parts = field_path.split(".", 1)
                if len(parts) == 2 and parts[1] in extra:
                    if cause in causes:
                        extra[parts[1]] = _REDACTED_SENTINEL
                        redacted.append(field_path)

    d["redacted_fields"] = redacted
    d["redaction_catalog"] = catalog
    d["redaction_default"] = list(causes)
    return d


def sidecar_to_json(sc: SessionSidecar, *, strip_sensitive: bool = True) -> str:
    """JSON string, compact but readable."""
    return json.dumps(
        sidecar_to_dict(sc, strip_sensitive=strip_sensitive),
        indent=2,
        ensure_ascii=False,
        default=str,
    )


def sidecar_sha256(sc: SessionSidecar) -> str:
    """Content‑addressable hash of the redacted (default) JSON."""
    return hashlib.sha256(
        sidecar_to_json(sc, strip_sensitive=True).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------
def write_sidecar(sc: SessionSidecar, *, strip_sensitive: bool = True) -> Path:
    """Write *sc* as a JSON sidecar and append to the daily NDJSON aggregate.

    Returns the path to the per‑run sidecar file.
    """
    date_prefix = sc.created_at[:10] if sc.created_at else datetime.now(timezone.utc).strftime("%Y-%m-%d")  # noqa: E501
    sidecar_dir = _ensure_sidecar_dir(date_prefix)

    # Individual sidecar
    run_file = sidecar_dir / f"{sc.run_id}.json"
    data = sidecar_to_dict(sc, strip_sensitive=strip_sensitive)
    if strip_sensitive:
        data["_redacted"] = True
    run_file.write_text(
        json.dumps(data, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    # Append to daily NDJSON aggregate (unredacted for local use)
    daily_file = sidecar_dir / "daily.ndjson"
    with daily_file.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                sidecar_to_dict(sc, strip_sensitive=False),
                ensure_ascii=False,
                default=str,
            )
            + "\n"
        )

    logger.info("Wrote sidecar %s (%s / %s entries)", run_file, sc.status, len(sc.evidence_refs))
    return run_file


# ---------------------------------------------------------------------------
# Reader / consumer API
# ---------------------------------------------------------------------------
def read_sidecar(run_id: str, *, date_prefix: str = "") -> Optional[SessionSidecar]:
    """Load a single sidecar by run_id, optionally scoped to a date prefix."""
    if not date_prefix:
        # Search most‑recent first (up to 7 days)
        base = get_hermes_home() / "sidecars"
        if not base.exists():
            return None
        candidates = sorted(base.iterdir(), reverse=True)[:7]
        for day_dir in candidates:
            p = day_dir / f"{run_id}.json"
            if p.exists():
                return _json_to_sidecar(p)
        return None

    p = _ensure_sidecar_dir(date_prefix) / f"{run_id}.json"
    if p.exists():
        return _json_to_sidecar(p)
    return None


def query_sidecars(*, date_prefix: str = "", entrypoint: str = "",
                   status: str = "", limit: int = 50) -> List[SessionSidecar]:
    """Query sidecars by optional date, entrypoint, and status filters."""
    base = get_hermes_home() / "sidecars"
    if not base.exists():
        return []

    results: List[SessionSidecar] = []
    target_dirs: List[Path] = []

    if date_prefix:
        d = base / date_prefix
        if d.exists():
            target_dirs = [d]
    else:
        target_dirs = sorted(base.iterdir(), reverse=True)[:14]

    for day_dir in target_dirs:
        daily = day_dir / "daily.ndjson"
        if not daily.exists():
            continue
        with daily.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    sc = _dict_to_sidecar(json.loads(line))
                except Exception:
                    continue
                if entrypoint and sc.entrypoint != entrypoint:
                    continue
                if status and sc.status != status:
                    continue
                results.append(sc)
                if len(results) >= limit:
                    return results
    return results


def aggregate_sidecars(*, date_prefix: str = "") -> Dict[str, Any]:
    """Produce a summary dict over all sidecars for a given date."""
    results = query_sidecars(date_prefix=date_prefix, limit=5000)
    total = len(results)
    by_entrypoint: Dict[str, int] = {}
    by_status: Dict[str, int] = {}
    degraded_flags_total: int = 0
    total_duration_s: float = 0.0

    for sc in results:
        by_entrypoint[sc.entrypoint] = by_entrypoint.get(sc.entrypoint, 0) + 1
        by_status[sc.status] = by_status.get(sc.status, 0) + 1
        degraded_flags_total += len(sc.degraded_flags)
        total_duration_s += sc.duration_s

    return {
        "date": date_prefix or "all",
        "total_runs": total,
        "by_entrypoint": by_entrypoint,
        "by_status": by_status,
        "total_degraded_flags": degraded_flags_total,
        "total_duration_s": round(total_duration_s, 3),
        "evidence_refs_count": sum(len(sc.evidence_refs) for sc in results),
        "artifacts_count": sum(len(sc.artifacts) for sc in results),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _json_to_sidecar(path: Path) -> Optional[SessionSidecar]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return _dict_to_sidecar(data)
    except Exception as exc:
        logger.warning("Failed to parse sidecar %s: %s", path, exc)
        return None


def _dict_to_sidecar(d: Dict[str, Any]) -> SessionSidecar:
    """Reconstruct a SessionSidecar from a parsed JSON dict.

    Tolerates missing or extra keys so older sidecars remain readable.
    """
    sc = SessionSidecar(
        schema_version=d.get("schema_version", "0.0.0"),
        run_id=d.get("run_id", ""),
        entrypoint=d.get("entrypoint", ""),
        session_id=d.get("session_id", ""),
        task_id=d.get("task_id", ""),
        created_at=d.get("created_at", ""),
        duration_s=d.get("duration_s", 0.0),
        status=d.get("status", "unknown"),
        error_class=d.get("error_class", ""),
        error_message=d.get("error_message", ""),
        degraded_flags=d.get("degraded_flags", []),
        model=d.get("model", ""),
        provider=d.get("provider", ""),
        platform=d.get("platform", ""),
        evidence_refs=d.get("evidence_refs", []),
        redacted_fields=d.get("redacted_fields", []),
        redaction_catalog=d.get("redaction_catalog", {}),
        redaction_default=set(d.get("redaction_default", [])),
        extra=d.get("extra", {}),
        lifecycle_transitions=d.get("lifecycle_transitions", []),
    )
    for a in d.get("artifacts", []):
        sc.artifacts.append(ArtifactRef(
            path=a.get("path", ""),
            kind=a.get("kind", "file"),
            sha256=a.get("sha256", ""),
            size_bytes=a.get("size_bytes", 0),
            description=a.get("description", ""),
        ))
    return sc


def strip_sensitive(sc: SessionSidecar) -> SessionSidecar:
    """Return a deep‑copy of *sc* with all sensitive fields redacted.

    Convenience wrapper around :func:`sidecar_to_dict` + roundtrip.
    """
    d = sidecar_to_dict(sc, strip_sensitive=True)
    return _dict_to_sidecar(d)
