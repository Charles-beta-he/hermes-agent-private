from pathlib import Path

import scripts.workflow_fail_closed_posture as posture


def test_fail_closed_posture_enabled_from_current_artifacts():
    report = posture.build_report()
    assert report["status"] == "FAIL_CLOSED_POSTURE_ENABLED"
    assert report["fail_closed_enabled"] is True
    assert report["read_only"] is True
    assert report["forbidden_enabled_observations"] == []
    assert all(report["checks"].values())
    assert report["observed_safe_defaults"]["external_side_effects_enabled"] is False
    assert report["observed_safe_defaults"]["unattended_dispatch_enabled"] is False
    assert report["observed_safe_defaults"]["publication_apply_enabled"] is False
    assert report["observed_safe_defaults"]["generic_adapter_write_through_enabled"] is False
    assert report["observed_safe_defaults"]["taskpool_enqueue_enabled"] is False
    assert report["observed_safe_defaults"]["executor_enabled"] is False
    assert report["posture_digest"]


def test_fail_closed_posture_degrades_when_required_source_missing(monkeypatch, tmp_path):
    fake_inputs = dict(posture.INPUTS)
    fake_inputs["runtime_readiness"] = tmp_path / "missing.json"
    monkeypatch.setattr(posture, "INPUTS", fake_inputs)

    report = posture.build_report()

    assert report["status"] == "FAIL_CLOSED_POSTURE_DEGRADED"
    assert report["fail_closed_enabled"] is False
    assert "runtime_readiness" in report["missing_sources"]
    assert report["checks"]["sources_present"] is False


def test_fail_closed_posture_write_report(tmp_path, monkeypatch):
    monkeypatch.setattr(posture, "OUT", tmp_path)
    report = posture.build_report()
    artifacts = posture.write_report(report, tmp_path)
    assert Path(artifacts["json"]).exists()
    assert Path(artifacts["markdown"]).exists()
    assert "FAIL_CLOSED_POSTURE_ENABLED" in Path(artifacts["markdown"]).read_text()
