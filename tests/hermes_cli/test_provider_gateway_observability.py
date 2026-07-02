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
