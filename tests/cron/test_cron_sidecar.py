from __future__ import annotations

import importlib


def test_emit_cron_session_sidecar_records_cron_and_script_metadata(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))

    import hermes_constants
    import cron.scheduler as sched
    import session_sidecar

    importlib.reload(hermes_constants)
    sched = importlib.reload(sched)
    session_sidecar = importlib.reload(session_sidecar)

    output_file = tmp_path / "cron-output.md"
    output_file.write_text("hello from cron\n", encoding="utf-8")

    meta = sched._emit_cron_session_sidecar(
        job={
            "id": "job-sidecar-1",
            "name": "Nightly Digest",
            "schedule": "0 9 * * *",
            "deliver": "local",
            "no_agent": True,
            "script": "watchdog.sh",
        },
        run_success=True,
        final_success=True,
        output="hello from cron\n",
        final_response=sched.SILENT_MARKER,
        error=None,
        delivery_error=None,
        output_file=output_file,
        duration_s=2.5,
    )

    assert meta is not None
    loaded = session_sidecar.read_sidecar(str(meta["run_id"]))
    assert loaded is not None
    assert loaded.entrypoint == "cron"
    assert loaded.status == "succeeded"
    assert loaded.extra["mode"] == "no_agent"
    assert loaded.extra["script_path"] == "watchdog.sh"
    assert loaded.extra["silent_run"] is True
    assert f"artifact:{output_file}" in loaded.evidence_refs
    assert any(ref.startswith("proof:session-sidecar:run:") for ref in loaded.evidence_refs)
