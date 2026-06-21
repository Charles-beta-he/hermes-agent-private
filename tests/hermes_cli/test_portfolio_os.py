from __future__ import annotations

import json
from pathlib import Path

from hermes_cli.portfolio_os import portfolio_status, run_doctor, strict_verify


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _seed_portfolio(home: Path) -> None:
    root = home / "workflows" / "portfolio" / "project-portfolio-os"
    profile_dir = home / "project-profiles"
    ledger = root / "progress-ledger.md"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    ledger.write_text("# Progress Ledger\n\nvalidated\n")
    for rel in [
        root / "00-model" / "portfolio-os.md",
        root / "02-literature" / "core-works-and-arguments.md",
        root / "03-governance" / "merge-and-abstraction-governance.md",
        root / "04-merge-candidates" / "initial-project-relations.md",
        root / "05-status" / "portfolio-status.md",
        root / "05-status" / "production-receipt.md",
    ]:
        rel.parent.mkdir(parents=True, exist_ok=True)
        rel.write_text("ok\n")
    _write_json(
        profile_dir / "project-portfolio-os.json",
        {
            "project": "project-portfolio-os",
            "project_type": "portfolio_control_plane",
            "ssot_root": str(root),
            "progress_ledger": str(ledger),
            "portfolio_profile": True,
            "promotion_policy": "evidence-gated ratchet",
            "relations": [],
            "current_components": {},
        },
    )
    _write_json(
        profile_dir / "uvisa-app-2.0.json",
        {
            "project": "uvisa-app-2.0",
            "project_type": "business_application",
            "ssot_root": str(home / "workflows" / "projects" / "uvisa-app-2.0"),
            "progress_ledger": str(home / "workflows" / "projects" / "uvisa-app-2.0" / "progress-ledger.md"),
            "portfolio_profile": True,
            "promotion_policy": "evidence-gated ratchet",
            "relations": [
                {"relation": "governed_by", "target": "project-portfolio-os", "merge_policy": "relation_only"}
            ],
            "current_components": {},
            "portfolio_code_guard": "Do not modify UVISA project code from portfolio governance work.",
        },
    )
    uv_root = home / "workflows" / "projects" / "uvisa-app-2.0"
    uv_root.mkdir(parents=True, exist_ok=True)
    (uv_root / "progress-ledger.md").write_text("# Progress Ledger\n\nmetadata only\n")
    _write_json(
        root / "01-registry" / "portfolio-registry.json",
        {
            "projects": [
                {"id": "project-portfolio-os"},
                {"id": "uvisa-app-2.0"},
            ],
            "relations": [
                {"from": "uvisa-app-2.0", "target": "project-portfolio-os", "relation": "governed_by"}
            ],
        },
    )


def test_portfolio_doctor_and_verify_ok(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    doctor = run_doctor(tmp_path)
    assert doctor["ok"] is True
    assert doctor["profile_count"] == 2
    verified = strict_verify(tmp_path)
    assert verified["ok"] is True
    assert verified["findings"] == []


def test_portfolio_doctor_fails_closed_on_missing_uvisa_guard(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    profile = tmp_path / "project-profiles" / "uvisa-app-2.0.json"
    data = json.loads(profile.read_text())
    data.pop("portfolio_code_guard")
    profile.write_text(json.dumps(data))
    doctor = run_doctor(tmp_path)
    assert doctor["ok"] is False
    assert any(f["check"] == "uvisa_code_guard" for f in doctor["findings"])


def test_portfolio_status_lists_projects(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    status = portfolio_status(tmp_path)
    ids = {p["id"] for p in status["projects"]}
    assert ids == {"project-portfolio-os", "uvisa-app-2.0"}


def test_portfolio_doctor_rejects_malformed_profile_types(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    profile = tmp_path / "project-profiles" / "project-portfolio-os.json"
    data = json.loads(profile.read_text())
    data["portfolio_profile"] = "yes"
    data["current_components"] = []
    data["relations"] = [{"relation": "governed_by", "target": "uvisa-app-2.0"}]
    profile.write_text(json.dumps(data))
    doctor = run_doctor(tmp_path)
    checks = {f["check"] for f in doctor["findings"]}
    assert doctor["ok"] is False
    assert "portfolio_profile" in checks
    assert "current_components" in checks
    assert "merge_policy" in checks


def test_portfolio_doctor_rejects_non_regular_progress_ledger(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    profile = tmp_path / "project-profiles" / "project-portfolio-os.json"
    data = json.loads(profile.read_text())
    ledger_dir = tmp_path / "ledger-dir"
    ledger_dir.mkdir()
    data["progress_ledger"] = str(ledger_dir)
    profile.write_text(json.dumps(data))
    doctor = run_doctor(tmp_path)
    assert doctor["ok"] is False
    assert any(
        f["check"] == "progress_ledger" and "not a regular file" in f["message"]
        for f in doctor["findings"]
    )


def test_portfolio_doctor_rejects_malformed_registry_shapes(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    registry = tmp_path / "workflows" / "portfolio" / "project-portfolio-os" / "01-registry" / "portfolio-registry.json"
    registry.write_text(json.dumps({"projects": {"id": "project-portfolio-os"}, "relations": "bad"}))
    doctor = run_doctor(tmp_path)
    checks = {f["check"] for f in doctor["findings"]}
    assert doctor["ok"] is False
    assert "registry_projects" in checks
    assert "registry_relations" in checks


def test_portfolio_doctor_detects_duplicate_project_ids(tmp_path: Path) -> None:
    _seed_portfolio(tmp_path)
    original = tmp_path / "project-profiles" / "project-portfolio-os.json"
    duplicate = tmp_path / "project-profiles" / "duplicate.json"
    duplicate.write_text(original.read_text())
    doctor = run_doctor(tmp_path)
    assert doctor["ok"] is False
    assert any(f["check"] == "duplicate_project_id" for f in doctor["findings"])
