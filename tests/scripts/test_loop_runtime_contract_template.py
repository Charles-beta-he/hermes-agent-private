from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "loop_runtime_contract_template.py"
DOCTOR = Path(__file__).resolve().parents[2] / "scripts" / "loop_runtime_contract_doctor.py"


def load_template():
    spec = importlib.util.spec_from_file_location("loop_runtime_contract_template", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_doctor():
    spec = importlib.util.spec_from_file_location("loop_runtime_contract_doctor", DOCTOR)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_control_tower_runtime_candidate_is_fail_closed_and_doctor_passes():
    template = load_template()
    doctor = load_doctor()

    contract = template.control_tower_runtime_promotion_contract()
    receipt = doctor.build_receipt(contract)

    assert contract["contract_id"] == "hermes.workflow_kernel.control_tower_runtime_promotion"
    assert contract["authority_tier"] == "local-reversible-dry-run"
    assert contract["scheduler"]["enabled"] is False
    assert contract["loop_runtime_enabled"] is False
    assert contract["external_side_effects"] is False
    assert "activate_control_tower_runtime_api" in contract["human_gate"]["required_before"]
    assert "promote_mission_executor" in contract["human_gate"]["required_before"]
    assert contract["evaluator"]["independent_from_generator"] is True
    assert contract["evaluator"]["can_veto"] is True
    assert receipt["status"] == "PASS"


def test_template_write_outputs_contract_and_doctor_receipt(tmp_path):
    template = load_template()

    result = template.write_candidate(template.control_tower_runtime_promotion_contract(), tmp_path)

    contract_path = Path(result["contract"])
    receipt_path = Path(result["receipt"])
    assert contract_path.exists()
    assert receipt_path.exists()
    contract = json.loads(contract_path.read_text())
    receipt = json.loads(receipt_path.read_text())
    assert contract["contract_id"] == receipt["contract_id"]
    assert receipt["status"] == "PASS"
    assert receipt["scheduler_enabled"] is False
    assert receipt["external_side_effects"] is False


def test_template_cli_json_writes_local_artifacts(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--candidate", "control_tower_runtime_promotion", "--write", "--out-dir", str(tmp_path), "--json"],
        text=True,
        capture_output=True,
        check=True,
    )
    result = json.loads(proc.stdout)
    assert result["candidate"] == "control_tower_runtime_promotion"
    assert result["receipt"]["status"] == "PASS"
    assert Path(result["artifacts"]["contract"]).exists()
    assert Path(result["artifacts"]["receipt"]).exists()
