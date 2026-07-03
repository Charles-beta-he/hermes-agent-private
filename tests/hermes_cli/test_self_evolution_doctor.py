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


def test_self_evolution_builds_experience_candidate_and_dry_run_skill_diff(tmp_path):
    evidence = tmp_path / "evidence.json"
    _write_sample_evidence(evidence)

    from hermes_cli.self_evolution_doctor import build_self_evolution_candidate_pack

    pack = build_self_evolution_candidate_pack(evidence_path=evidence)

    assert pack["schema_version"] == "hermes.self_evolution.candidate_pack.v1"
    assert pack["mode"] == "proposal_only"
    assert pack["execution"]["direct_skill_write"] is False
    assert pack["execution"]["direct_memory_write"] is False
    assert pack["experience_atom"]["status"] == "promoted_candidate"
    assert pack["experience_atom"]["landing_zone"] == "skill"
    assert pack["experience_atom"]["claim"].startswith("Hermes can consume Raven/EverOS")
    assert "agent skill SKILL.md" in pack["experience_atom"]["falsifiers"][0]
    assert pack["dry_run_skill_diff"]["target_skill"] == "hermes-agent"
    assert pack["dry_run_skill_diff"]["write_enabled"] is False
    assert "```diff" in pack["dry_run_skill_diff"]["preview"]


def test_self_evolution_materializes_candidate_pack_without_writing_skills(tmp_path):
    evidence = tmp_path / "evidence.json"
    _write_sample_evidence(evidence)
    output = tmp_path / "candidate-pack.json"

    from hermes_cli.self_evolution_doctor import materialize_self_evolution_candidate_pack

    result = materialize_self_evolution_candidate_pack(evidence_path=evidence, output_path=output)

    assert result["written"] is True
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["schema_version"] == "hermes.self_evolution.candidate_pack.v1"
    assert payload["dry_run_skill_diff"]["write_enabled"] is False
    assert payload["execution"]["direct_skill_write"] is False


def test_run_doctor_self_evolution_can_materialize_candidate_pack(monkeypatch, tmp_path, capsys):
    from hermes_cli import doctor as doctor_mod

    output = tmp_path / "candidate-pack.json"
    calls = []

    def fake_materialize(*, evidence_path=None, output_path):
        calls.append((evidence_path, output_path))
        return {"written": True, "output_path": str(output_path), "status": "ok", "kind": "candidate_pack"}

    monkeypatch.setattr(
        "hermes_cli.self_evolution_doctor.materialize_self_evolution_candidate_pack",
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
            self_evolution_output=None,
            self_evolution_candidate_output=str(output),
            json=True,
        )
    )

    assert calls == [("evidence.json", str(output))]
    assert json.loads(capsys.readouterr().out)["kind"] == "candidate_pack"


def test_self_evolution_builds_patch_packet_from_candidate_pack(tmp_path):
    evidence = tmp_path / "evidence.json"
    _write_sample_evidence(evidence)
    candidate = tmp_path / "candidate.json"

    from hermes_cli.self_evolution_doctor import (
        materialize_self_evolution_candidate_pack,
        build_self_evolution_patch_packet,
    )

    materialize_self_evolution_candidate_pack(evidence_path=evidence, output_path=candidate)
    packet = build_self_evolution_patch_packet(candidate_pack_path=candidate)

    assert packet["schema_version"] == "hermes.self_evolution.patch_packet.v1"
    assert packet["mode"] == "fixture_only"
    assert packet["execution"]["direct_skill_write"] is False
    assert packet["patch"]["target_skill"] == "hermes-agent"
    assert packet["patch"]["real_target_write_enabled"] is False
    assert packet["patch"]["operations"][0]["op"] == "append_reference_addendum"
    assert "Reference addendum" in packet["patch"]["operations"][0]["text"]


def test_self_evolution_verifies_patch_packet_rejects_deletions(tmp_path):
    from hermes_cli.self_evolution_doctor import verify_self_evolution_patch_packet

    bad = {
        "schema_version": "hermes.self_evolution.patch_packet.v1",
        "mode": "fixture_only",
        "execution": {"direct_skill_write": False, "direct_memory_write": False},
        "patch": {
            "target_skill": "hermes-agent",
            "real_target_write_enabled": False,
            "operations": [{"op": "delete", "text": "remove old content"}],
        },
    }

    verdict = verify_self_evolution_patch_packet(bad)

    assert verdict["ok"] is False
    assert "unsupported_operation" in verdict["reasons"]


def test_self_evolution_applies_patch_to_temp_skill_fixture_only(tmp_path):
    evidence = tmp_path / "evidence.json"
    _write_sample_evidence(evidence)
    candidate = tmp_path / "candidate.json"
    fixture_dir = tmp_path / "fixture-skill"
    fixture_dir.mkdir()
    (fixture_dir / "SKILL.md").write_text("# Hermes Agent\n\nExisting body.\n", encoding="utf-8")

    from hermes_cli.self_evolution_doctor import (
        materialize_self_evolution_candidate_pack,
        build_self_evolution_patch_packet,
        apply_self_evolution_patch_to_fixture,
    )

    materialize_self_evolution_candidate_pack(evidence_path=evidence, output_path=candidate)
    packet = build_self_evolution_patch_packet(candidate_pack_path=candidate)
    receipt = apply_self_evolution_patch_to_fixture(packet=packet, fixture_skill_dir=fixture_dir)

    assert receipt["schema_version"] == "hermes.self_evolution.fixture_apply_receipt.v1"
    assert receipt["real_target_touched"] is False
    assert receipt["fixture_touched"] is True
    updated = (fixture_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "Existing body." in updated
    assert "self-evolution absorption boundary" in updated


def test_run_doctor_self_evolution_can_materialize_patch_packet(monkeypatch, tmp_path, capsys):
    from hermes_cli import doctor as doctor_mod

    output = tmp_path / "patch-packet.json"
    calls = []

    def fake_materialize(*, candidate_pack_path, output_path):
        calls.append((candidate_pack_path, output_path))
        return {"written": True, "output_path": str(output_path), "status": "ok", "kind": "patch_packet"}

    monkeypatch.setattr(
        "hermes_cli.self_evolution_doctor.materialize_self_evolution_patch_packet",
        fake_materialize,
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
            self_evolution_candidate_output=None,
            self_evolution_patch_packet_output=str(output),
            self_evolution_candidate_input="candidate.json",
            self_evolution_fixture_skill_dir=None,
            json=True,
        )
    )

    assert calls == [("candidate.json", str(output))]
    assert json.loads(capsys.readouterr().out)["kind"] == "patch_packet"
