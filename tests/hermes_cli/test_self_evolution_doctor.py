from __future__ import annotations

import json
from argparse import Namespace


def _write_sample_evidence(path, *, memory=True, agent_case=True, skill=False):
    path.write_text(
        json.dumps(
            {
                "claims": {
                    "authorized_test_real_user_memory_roundtrip_proven": memory,
                    "authorized_test_agent_case_pipeline_proven": agent_case,
                    "authorized_test_agent_skill_skill_md_proven": skill,
                },
                "authorized_test_final_readback": {
                    "strict_claims": {
                        "real_user_memory_roundtrip_proven": memory,
                        "agent_case_pipeline_proven": agent_case,
                        "agent_skill_skil_md_proven": skill,
                        "no_benchmark_daemon_retained": True,
                    },
                    "qwen25_mxbai_artifacts": {
                        "counts": {
                            "user_profiles": 1 if memory else 0,
                            "agent_cases": 1 if agent_case else 0,
                            "agent_skills": 1 if skill else 0,
                        },
                        "md_files": ["/tmp/user.md", "/tmp/agent_case.md"],
                    },
                },
            }
        ),
        encoding="utf-8",
    )


def test_self_evolution_report_converts_upstream_evidence_to_proposal_only_packet(tmp_path):
    evidence = tmp_path / "upstream-self-evolution-benchmark.json"
    _write_sample_evidence(evidence)

    from hermes_cli.self_evolution_doctor import build_self_evolution_report

    report = build_self_evolution_report(evidence_path=evidence)

    assert report["schema_version"] == "hermes.self_evolution.proposal_doctor.v1"
    assert report["mode"] == "proposal_only"
    assert report["execution"]["runtime_mutation"] is False
    assert report["execution"]["external_dispatch"] is False
    assert report["execution"]["direct_memory_write"] is False
    assert report["execution"]["direct_skill_write"] is False
    assert report["capability_level"] == "memory_roundtrip_and_agent_case_proven"
    assert report["proposal_packet"]["landing_zone"] == "experience_ratchet_proposal"
    assert report["proposal_packet"]["promotion_target"] == "skill_or_memory_after_separate_gate"
    assert report["proposal_packet"]["auto_promote"] is False
    assert report["proposal_packet"]["evidence_path"] == str(evidence)
    assert report["readiness"]["ready_for_default_off_local_harness"] is True
    assert report["readiness"]["ready_for_direct_skill_write"] is False


def test_self_evolution_report_missing_evidence_is_safe_off(tmp_path):
    from hermes_cli.self_evolution_doctor import build_self_evolution_report

    report = build_self_evolution_report(evidence_path=tmp_path / "missing.json")

    assert report["status"] == "missing_evidence"
    assert report["mode"] == "proposal_only"
    assert report["execution"]["runtime_mutation"] is False
    assert report["proposal_packet"]["auto_promote"] is False
    assert report["readiness"]["ready_for_default_off_local_harness"] is False


def test_run_doctor_self_evolution_json_fast_path(monkeypatch, capsys):
    from hermes_cli import doctor as doctor_mod

    monkeypatch.setattr(
        "hermes_cli.self_evolution_doctor.build_self_evolution_report",
        lambda evidence_path=None: {
            "schema_version": "hermes.self_evolution.proposal_doctor.v1",
            "mode": "proposal_only",
            "status": "ok",
        },
    )

    doctor_mod.run_doctor(
        Namespace(
            fix=False,
            ack=None,
            external_data=False,
            provider_gateway_observability=False,
            self_evolution=True,
            self_evolution_evidence=None,
            self_evolution_output=None,
            json=True,
        )
    )

    out = capsys.readouterr().out
    assert json.loads(out)["schema_version"] == "hermes.self_evolution.proposal_doctor.v1"


def test_run_doctor_self_evolution_can_materialize_local_receipt(monkeypatch, tmp_path, capsys):
    from hermes_cli import doctor as doctor_mod

    receipt = tmp_path / "receipt.json"
    calls = []

    def fake_materialize(*, evidence_path=None, output_path):
        calls.append((evidence_path, output_path))
        receipt.write_text(json.dumps({"schema_version": "hermes.self_evolution.proposal_receipt.v1"}), encoding="utf-8")
        return {"written": True, "output_path": str(output_path), "status": "ok"}

    monkeypatch.setattr(
        "hermes_cli.self_evolution_doctor.materialize_self_evolution_proposal",
        fake_materialize,
    )

    doctor_mod.run_doctor(
        Namespace(
            fix=False,
            ack=None,
            external_data=False,
            provider_gateway_observability=False,
            self_evolution=True,
            self_evolution_evidence="evidence.json",
            self_evolution_output=str(receipt),
            json=True,
        )
    )

    assert calls == [("evidence.json", str(receipt))]
    out = capsys.readouterr().out
    assert json.loads(out)["output_path"] == str(receipt)


def test_self_evolution_materializes_local_proposal_receipt_without_promotion(tmp_path):
    evidence = tmp_path / "evidence.json"
    _write_sample_evidence(evidence)
    receipt = tmp_path / "proposal.json"

    from hermes_cli.self_evolution_doctor import materialize_self_evolution_proposal

    result = materialize_self_evolution_proposal(evidence_path=evidence, output_path=receipt)

    assert result["written"] is True
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "hermes.self_evolution.proposal_receipt.v1"
    assert payload["execution"]["runtime_mutation"] is False
    assert payload["execution"]["direct_skill_write"] is False
    assert payload["execution"]["direct_memory_write"] is False
    assert payload["proposal_packet"]["auto_promote"] is False
    assert payload["proposal_packet"]["capability_level"] == "memory_roundtrip_and_agent_case_proven"
