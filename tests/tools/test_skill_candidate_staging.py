from __future__ import annotations

import json
from pathlib import Path

from tools.skill_candidate_staging import stage_skill_candidate


def _skill(path: Path, name: str, description: str, body: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"""---
name: {name}
description: {description}
---

# {name}

{body or 'Use this skill for a narrow, evidence-backed workflow.'}
""",
        encoding="utf-8",
    )
    return path


def test_stage_skill_candidate_writes_review_artifact_without_applying_patch(tmp_path):
    base = _skill(
        tmp_path / "installed" / "narrow" / "SKILL.md",
        "narrow",
        "Use when managing narrow release-note verification with deterministic evidence.",
        "Old body.\n",
    )
    proposed = _skill(
        tmp_path / "candidate" / "SKILL.md",
        "narrow",
        "Use when managing narrow release-note verification with deterministic evidence.",
        "Old body.\n\nNew proof-linked rollback step.\nSee references/release.md.\n",
    )
    (tmp_path / "candidate" / "references").mkdir()
    (tmp_path / "candidate" / "references" / "release.md").write_text("reference", encoding="utf-8")
    output = tmp_path / "stage" / "candidate.json"
    before = base.read_text(encoding="utf-8")

    report = stage_skill_candidate(
        base_skill=base,
        candidate_skill=proposed,
        evidence=["artifact:/tmp/proof.json", "command:pytest tests/tools/test_skill_candidate_staging.py"],
        verification=["python -m pytest tests/tools/test_skill_candidate_staging.py"],
        rollback=["discard staged artifact; no production skill was modified"],
        output=output,
    )

    assert report["schema_version"] == "hermes_skill_candidate_staging_v1"
    assert report["status"] == "staged"
    assert output.exists()
    assert base.read_text(encoding="utf-8") == before
    assert "New proof-linked rollback step" in report["patch"]["unified_diff"]
    assert report["checks"]["frontmatter"]["status"] == "pass"
    assert report["checks"]["trigger_breadth"]["status"] == "pass"
    assert report["checks"]["references"]["status"] == "pass"
    assert report["apply_policy"] == "review_only_no_direct_apply"
    written = json.loads(output.read_text(encoding="utf-8"))
    assert written["rollback"] == ["discard staged artifact; no production skill was modified"]


def test_stage_skill_candidate_rejects_broad_or_unevidenced_candidate(tmp_path):
    base = _skill(
        tmp_path / "installed" / "broad" / "SKILL.md",
        "broad",
        "Use when managing a specific, evidence-backed deployment workflow.",
    )
    proposed = _skill(
        tmp_path / "candidate" / "SKILL.md",
        "broad",
        "Use when doing anything and everything.",
        "Generic replacement.\n",
    )

    report = stage_skill_candidate(
        base_skill=base,
        candidate_skill=proposed,
        evidence=[],
        verification=["python -m pytest tests/tools/test_skill_candidate_staging.py"],
        rollback=["discard staged artifact"],
        output=tmp_path / "stage" / "rejected.json",
    )

    assert report["status"] == "rejected"
    assert "missing_evidence" in report["rejection_reasons"]
    assert "trigger_too_broad" in report["rejection_reasons"]
    assert report["checks"]["evidence"]["status"] == "fail"
    assert report["checks"]["trigger_breadth"]["status"] == "fail"


def test_stage_skill_candidate_rejects_missing_reference_file(tmp_path):
    base = _skill(
        tmp_path / "installed" / "refs" / "SKILL.md",
        "refs",
        "Use when managing reference-backed release note verification.",
    )
    proposed = _skill(
        tmp_path / "candidate" / "SKILL.md",
        "refs",
        "Use when managing reference-backed release note verification.",
        "See references/missing.md before applying.\n",
    )

    report = stage_skill_candidate(
        base_skill=base,
        candidate_skill=proposed,
        evidence=["artifact:/tmp/proof.json"],
        verification=["python -m pytest tests/tools/test_skill_candidate_staging.py"],
        rollback=["discard staged artifact"],
        output=tmp_path / "stage" / "missing-ref.json",
    )

    assert report["status"] == "rejected"
    assert "missing_referenced_file" in report["rejection_reasons"]
    assert report["checks"]["references"]["missing"] == ["references/missing.md"]


def test_stage_skill_candidate_allows_preexisting_unresolved_references(tmp_path):
    base = _skill(
        tmp_path / "installed" / "preexisting" / "SKILL.md",
        "preexisting",
        "Use when managing preexisting reference-backed release note verification.",
        "Existing mention: references/legacy-missing.md.\n",
    )
    proposed = _skill(
        tmp_path / "candidate" / "SKILL.md",
        "preexisting",
        "Use when managing preexisting reference-backed release note verification.",
        "Existing mention: references/legacy-missing.md.\nNew safe body.\n",
    )

    report = stage_skill_candidate(
        base_skill=base,
        candidate_skill=proposed,
        evidence=["artifact:/tmp/proof.json"],
        verification=["python -m pytest tests/tools/test_skill_candidate_staging.py"],
        rollback=["discard staged artifact"],
        output=tmp_path / "stage" / "preexisting-ref.json",
    )

    assert report["status"] == "staged"
    assert report["checks"]["references"]["introduced"] == []
    assert report["checks"]["references"]["missing"] == []
