"""
Tests for the session sidecard observation layer.

Covers:
  1. Schema dataclass construction and defaults
  2. Serialisation roundtrip (dict ↔ JSON ↔ dataclass)
  3. Redaction of sensitive fields
  4. write / read / query cycle
  5. Producers for each entrypoint
  6. Consumer aggregate / selfcheck / daily‑report
  7. Redaction invariance: sensitive evidence_refs stripped by default
  8. Degraded / failed sidecar roundtrip
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

# Patch hermes_constants before any session_sidecar import
import hermes_constants as hc
_real_home = hc.get_hermes_home()


def _test_home() -> Path:
    """Return a temp dir that sidecars will write into."""
    td = Path(tempfile.mkdtemp(prefix="sidecar_test_"))
    return td


# Monkey‑patch get_hermes_home so writes go to our temp dir
_TEST_HOME = _test_home()


def _patch_home():
    import hermes_constants
    hermes_constants.get_hermes_home = lambda: _TEST_HOME
    from session_sidecar import get_hermes_home as _f
    import session_sidecar as ss
    ss.get_hermes_home = lambda: _TEST_HOME


_patched = False


def _ensure_patched():
    global _patched
    if not _patched:
        _patch_home()
        _patched = True


def test_schema_defaults():
    _ensure_patched()
    from session_sidecar import SessionSidecar, SIDECAR_SCHEMA_VERSION

    sc = SessionSidecar()
    assert sc.schema_version == SIDECAR_SCHEMA_VERSION
    assert sc.run_id != ""  # auto‑generated
    assert sc.created_at != ""  # auto‑generated
    assert sc.status == "succeeded"
    assert sc.entrypoint == ""
    assert sc.degraded_flags == []
    assert sc.artifacts == []
    assert sc.evidence_refs == []
    assert sc.redacted_fields == []
    assert sc.redaction_catalog == {}
    assert sc.lifecycle_transitions == []
    assert sc.extra == {}


def test_serialisation_roundtrip():
    _ensure_patched()
    from session_sidecar import (
        SessionSidecar, ArtifactRef,
        sidecar_to_dict, sidecar_to_json,
        _dict_to_sidecar,
    )

    sc = SessionSidecar(
        run_id="test-001",
        entrypoint="cli",
        session_id="sess-1",
        model="deepseek-v4",
        provider="deepseek",
        duration_s=42.5,
        status="degraded",
        error_class="RateLimitWarning",
        degraded_flags=["partial_retry", "budget_warning"],
        evidence_refs=["proof:test-001", "artifact:/tmp/out.json"],
    )
    sc.artifacts.append(ArtifactRef(
        path="/tmp/output.txt", kind="file",
        size_bytes=1234, description="test output",
    ))

    # Dict roundtrip
    d = sidecar_to_dict(sc, strip_sensitive=False)
    sc2 = _dict_to_sidecar(d)
    assert sc2.run_id == sc.run_id
    assert sc2.entrypoint == sc.entrypoint
    assert sc2.status == sc.status
    assert sc2.duration_s == sc.duration_s
    assert sc2.error_class == sc.error_class
    assert sc2.degraded_flags == sc.degraded_flags
    assert sc2.evidence_refs == sc.evidence_refs
    assert len(sc2.artifacts) == 1
    assert sc2.artifacts[0].path == "/tmp/output.txt"

    # JSON roundtrip
    j = sidecar_to_json(sc, strip_sensitive=False)
    d2 = json.loads(j)
    sc3 = _dict_to_sidecar(d2)
    assert sc3.run_id == "test-001"
    assert sc3.model == "deepseek-v4"
    assert sc3.provider == "deepseek"


def test_redaction_pii():
    _ensure_patched()
    from session_sidecar import (
        SessionSidecar, ArtifactRef,
        sidecar_to_dict,
    )

    sc = SessionSidecar(run_id="redact-test-1")
    sc.artifacts.append(ArtifactRef(
        path="/Users/charles/secret.key",
        kind="file",
        redaction_cause="secret",
    ))
    sc.artifacts.append(ArtifactRef(
        path="/tmp/public-output.json",
        kind="file",
        description="safe output",
    ))

    d_redacted = sidecar_to_dict(sc, strip_sensitive=True)
    redacted_paths = [
        a["path"] for a in d_redacted["artifacts"]
    ]
    assert "**REDACTED**" in redacted_paths  # secret.key redacted
    assert "/tmp/public-output.json" in redacted_paths  # public kept

    d_unredacted = sidecar_to_dict(sc, strip_sensitive=False)
    paths_unredacted = [
        a["path"] for a in d_unredacted["artifacts"]
    ]
    assert "/Users/charles/secret.key" in paths_unredacted
    assert "/tmp/public-output.json" in paths_unredacted


def test_redaction_evidence_refs():
    _ensure_patched()
    from session_sidecar import SessionSidecar, sidecar_to_dict

    sc = SessionSidecar(run_id="redact-ev-1")
    sc.evidence_refs = [
        "proof:test-ok",
        "artifact:/tmp/safe.json",
        "secret=abcdef1234",
        "credential:some_api_key",
    ]

    d_redacted = sidecar_to_dict(sc, strip_sensitive=True)
    refs = d_redacted["evidence_refs"]
    assert "proof:test-ok" in refs                     # kept
    assert "artifact:/tmp/safe.json" in refs            # kept
    assert "**REDACTED**" in refs                       # secret/credential redacted
    assert "secret=abcdef1234" not in refs               # stripped
    assert len(d_redacted["redacted_fields"]) >= 2       # 2 refs redacted


def test_write_read_cycle():
    _ensure_patched()
    from session_sidecar import (
        SessionSidecar, write_sidecar, read_sidecar,
    )

    sc = SessionSidecar(
        run_id="cycle-test-001",
        entrypoint="cli",
        status="succeeded",
    )
    path = write_sidecar(sc)
    assert path.exists()
    assert path.suffix == ".json"

    loaded = read_sidecar("cycle-test-001")
    assert loaded is not None
    assert loaded.run_id == "cycle-test-001"
    assert loaded.entrypoint == "cli"

    # Verify daily.ndjson created
    daily = path.parent / "daily.ndjson"
    assert daily.exists()


def test_query_by_entrypoint():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar, query_sidecars

    for i, ep in enumerate(["cli", "cron", "automation"]):
        sc = SessionSidecar(run_id=f"q-{i:03d}", entrypoint=ep)
        write_sidecar(sc)

    cli_results = query_sidecars(entrypoint="cli")
    assert len(cli_results) >= 1
    assert all(s.entrypoint == "cli" for s in cli_results)

    cron_results = query_sidecars(entrypoint="cron")
    assert len(cron_results) >= 1
    assert all(s.entrypoint == "cron" for s in cron_results)


def test_aggregate():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar, aggregate_sidecars

    for status in ["succeeded", "degraded", "failed"]:
        sc = SessionSidecar(run_id=f"agg-{status}", entrypoint="cli", status=status)
        write_sidecar(sc)

    agg = aggregate_sidecars()
    assert agg["total_runs"] >= 3
    assert "cli" in agg.get("by_entrypoint", {})


def test_producers():
    _ensure_patched()
    from session_sidecar.producers import (
        cli_producer, cron_producer, browser_producer, script_producer,
    )

    sc_cli = cli_producer(session_id="sess-1", model="m1", duration_s=10.0)
    assert sc_cli.entrypoint == "cli"
    assert sc_cli.model == "m1"
    assert sc_cli.duration_s == 10.0

    sc_cron = cron_producer(job_id="j-1", job_name="nightly", duration_s=30.0)
    assert sc_cron.entrypoint == "cron"
    assert sc_cron.extra.get("job_name") == "nightly"

    sc_browser = browser_producer(
        session_key="b-1", url_count=5, screenshot_count=3, click_count=12,
    )
    assert sc_browser.entrypoint == "automation"
    assert sc_browser.extra["url_count"] == 5

    sc_script = script_producer(script_path="/tmp/test.py", duration_s=2.5)
    assert sc_script.entrypoint == "script"
    assert "test.py" in sc_script.extra["script_path"]


def test_consumer_selfcheck():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar
    from session_sidecar.consumer import selfcheck_probe, sidecar_evidence_for_selfcheck

    # Write at least one sidecar
    sc = SessionSidecar(run_id="selfcheck-test-1", entrypoint="cli", status="succeeded")
    write_sidecar(sc)

    probe = selfcheck_probe()
    assert "status" in probe
    assert "sidecar_dir" in probe

    evidence = sidecar_evidence_for_selfcheck()
    assert len(evidence) >= 1
    assert all(e["type"] == "proof:session-sidecar" for e in evidence)


def test_consumer_daily_report():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar
    from session_sidecar.consumer import daily_report_section

    sc = SessionSidecar(run_id="daily-test-1", entrypoint="cli", status="succeeded")
    write_sidecar(sc)

    section = daily_report_section()
    assert "Session Sidecar Summary" in section
    assert "Total runs" in section


def test_degraded_sidecar():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar, _dict_to_sidecar

    sc = SessionSidecar(
        run_id="degraded-001",
        entrypoint="cron",
        status="degraded",
        degraded_flags=["timeout_retry", "partial_output"],
        error_class="TimeoutError",
        error_message="Job exceeded 300s timeout, partial results captured",
    )
    d = write_sidecar(sc)
    assert d.exists()

    loaded_data = json.loads(d.read_text(encoding="utf-8"))
    loaded = _dict_to_sidecar(loaded_data)
    assert loaded.status == "degraded"
    assert "timeout_retry" in loaded.degraded_flags
    assert loaded.error_class == "TimeoutError"


def test_failed_sidecar():
    _ensure_patched()
    from session_sidecar import SessionSidecar, write_sidecar, read_sidecar

    sc = SessionSidecar(
        run_id="failed-001",
        entrypoint="automation",
        status="failed",
        error_class="RateLimitError",
        error_message="API rate limit exceeded, retry after 60s",
        degraded_flags=["api_throttle"],
    )
    write_sidecar(sc)

    loaded = read_sidecar("failed-001")
    assert loaded is not None
    assert loaded.status == "failed"
    assert loaded.error_class == "RateLimitError"


def test_strip_sensitive_preserves_structure():
    _ensure_patched()
    from session_sidecar import (
        SessionSidecar, ArtifactRef, strip_sensitive,
        sidecar_to_dict,
    )

    sc = SessionSidecar(run_id="strip-struct-1", entrypoint="cli", status="succeeded")
    sc.artifacts.append(ArtifactRef(path="/safe/path.txt", kind="file"))

    stripped = strip_sensitive(sc)
    assert stripped.run_id == sc.run_id
    assert stripped.entrypoint == sc.entrypoint
    assert stripped.status == sc.status
    assert len(stripped.artifacts) == 1
    assert stripped.artifacts[0].path == "/safe/path.txt"


def test_reject_unknown_entrypoint():
    _ensure_patched()
    import logging
    from session_sidecar import SessionSidecar

    logging.disable(logging.WARNING)  # suppress expected warning
    sc = SessionSidecar(entrypoint="unknown_mode")
    logging.disable(logging.NOTSET)

    assert sc.entrypoint == "unknown_mode"  # still set, but warned


def test_inject_sidecar_filters_taskpool_metadata_for_script():
    _ensure_patched()
    from session_sidecar import read_sidecar
    from session_sidecar.consumer import _inject_test_sidecar

    run_id = _inject_test_sidecar(
        status="succeeded",
        entrypoint="script",
        task_id="taskpool-task-id-that-script-producer-does-not-accept",
        evidence_refs="proof:script-chain,artifact:/tmp/script.json",
    )

    loaded = read_sidecar(run_id)
    assert loaded is not None
    assert loaded.entrypoint == "script"
    assert loaded.evidence_refs == ["proof:script-chain", "artifact:/tmp/script.json"]


def test_inject_sidecar_accepts_automation_alias_and_degraded_flags():
    _ensure_patched()
    from session_sidecar import read_sidecar
    from session_sidecar.consumer import _inject_test_sidecar

    run_id = _inject_test_sidecar(
        status="degraded",
        entrypoint="automation",
        task_id="task-123",
        degraded_flags="browser_tool_unavailable,partial_capture",
        error_class="BrowserUnavailable",
    )

    loaded = read_sidecar(run_id)
    assert loaded is not None
    assert loaded.entrypoint == "automation"
    assert loaded.task_id == "task-123"
    assert loaded.status == "degraded"
    assert loaded.degraded_flags == ["browser_tool_unavailable", "partial_capture"]
    assert loaded.error_class == "BrowserUnavailable"
