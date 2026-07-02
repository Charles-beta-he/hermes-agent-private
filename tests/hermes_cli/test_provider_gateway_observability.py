from __future__ import annotations

import json
from argparse import Namespace


def test_provider_gateway_observability_report_is_local_projection(tmp_path):
    from hermes_cli.provider_gateway_observability import build_provider_gateway_observability_report

    event_log = tmp_path / "events.jsonl"
    event_log.write_text(
        json.dumps(
            {
                "surface": "provider",
                "operation": "chat_completion",
                "provider": "openrouter",
                "status": "success",
                "latency_ms": 42,
                "metadata": {"api_key": "fixture-secret-value"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    report = build_provider_gateway_observability_report(event_log)

    assert report["schema_version"] == "hermes.provider_gateway_observability.doctor.v1"
    assert report["policy"]["external_telemetry"] == "forbidden_by_default"
    assert report["policy"]["credential_access"] == "forbidden"
    assert report["projection"]["status"] == "OK"
    assert report["projection"]["summary"]["event_count"] == 1
    assert "no network calls" in report["projection"]["safe_boundaries"]


def test_run_doctor_provider_gateway_observability_json_fast_path(monkeypatch, capsys):
    from hermes_cli import doctor as doctor_mod

    monkeypatch.setattr(
        "hermes_cli.provider_gateway_observability.build_provider_gateway_observability_report",
        lambda event_log=None: {
            "schema_version": "hermes.provider_gateway_observability.doctor.v1",
            "projection": {"status": "NO_DATA", "summary": {"event_count": 0}},
            "policy": {"external_telemetry": "forbidden_by_default"},
        },
    )

    doctor_mod.run_doctor(
        Namespace(
            fix=False,
            ack=None,
            external_data=False,
            provider_gateway_observability=True,
            provider_gateway_event_log="/tmp/demo.jsonl",
            json=True,
        )
    )

    out = capsys.readouterr().out
    loaded = json.loads(out)
    assert loaded["projection"]["status"] == "NO_DATA"
    assert loaded["policy"]["external_telemetry"] == "forbidden_by_default"



def test_default_event_log_uses_hermes_home(monkeypatch, tmp_path):
    from hermes_cli import provider_gateway_observability as mod

    monkeypatch.setattr(mod, "get_hermes_home", lambda: tmp_path)

    assert mod._default_event_log() == tmp_path / "state" / "provider_gateway_observability" / "events.jsonl"


def test_load_projection_module_missing_script(monkeypatch, tmp_path):
    from hermes_cli import provider_gateway_observability as mod

    monkeypatch.setattr(mod, "get_project_root", lambda: tmp_path)

    try:
        mod._load_projection_module()
    except FileNotFoundError as exc:
        assert "provider gateway observability script not found" in str(exc)
    else:
        raise AssertionError("missing script should fail")


def test_load_projection_module_without_loader(monkeypatch, tmp_path):
    from hermes_cli import provider_gateway_observability as mod

    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    (script_dir / "provider_gateway_observability.py").write_text("VALUE = 1\n", encoding="utf-8")
    monkeypatch.setattr(mod, "get_project_root", lambda: tmp_path)
    monkeypatch.setattr(mod.importlib.util, "spec_from_file_location", lambda *_args, **_kwargs: None)

    try:
        mod._load_projection_module()
    except ImportError as exc:
        assert "could not load provider gateway observability script" in str(exc)
    else:
        raise AssertionError("loader-less spec should fail")


def test_build_report_error_projection_is_fail_closed(monkeypatch, tmp_path):
    from hermes_cli import provider_gateway_observability as mod

    def boom():
        raise RuntimeError("fixture import failure")

    monkeypatch.setattr(mod, "_load_projection_module", boom)

    report = mod.build_provider_gateway_observability_report(tmp_path / "events.jsonl")

    assert report["projection"]["status"] == "ERROR"
    assert report["projection"]["summary"] == {"event_count": 0}
    assert "RuntimeError: fixture import failure" in report["projection"]["error"]
    assert "no network calls" in report["projection"]["safe_boundaries"]


def test_print_provider_gateway_observability_report_text(monkeypatch, capsys):
    from hermes_cli import provider_gateway_observability as mod

    monkeypatch.setattr(
        mod,
        "build_provider_gateway_observability_report",
        lambda event_log=None: {
            "projection": {
                "status": "OK",
                "event_log": "/tmp/events.jsonl",
                "summary": {"event_count": 3},
                "safe_boundaries": ["local JSONL only"],
            }
        },
    )

    mod.print_provider_gateway_observability_report(as_json=False)

    out = capsys.readouterr().out
    assert "Provider/Gateway Observability: OK" in out
    assert "event_log: /tmp/events.jsonl" in out
    assert "events: 3" in out
    assert "boundaries: local JSONL only" in out
