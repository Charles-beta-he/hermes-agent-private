from __future__ import annotations

from tools.skills_hub import SkillBundle
from tools.skills_intake_audit import audit_candidates, evaluate_bundle


class FakeGitHubSource:
    def __init__(self, tree_entries):
        self._tree_entries = tree_entries

    def _get_repo_tree(self, repo: str):
        return ("main", self._tree_entries)


class FakeMeta:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


class DummySource:
    def __init__(self, source_name: str = "dummy"):
        self._source_name = source_name

    def source_id(self):
        return self._source_name


def _bundle(identifier: str, skill_md: str, **extra_files: str) -> SkillBundle:
    files: dict[str, str | bytes] = {"SKILL.md": skill_md}
    files.update(extra_files)
    return SkillBundle(
        name=identifier.split("/")[-1],
        files=files,
        source="github",
        identifier=identifier,
        trust_level="community",
    )


def _mock_source_evidence(repo: str = "acme/repo", path: str = "skill"):
    return {
        "repo": repo,
        "path": path,
        "repo_url": f"https://github.com/{repo}",
        "skill_url": f"https://github.com/{repo}/tree/main/{path}",
        "default_branch": "main",
        "head_commit": {
            "ref": "main",
            "sha": "abc123",
            "html_url": f"https://github.com/{repo}/commit/abc123",
            "api_url": f"https://api.github.com/repos/{repo}/commits/main",
            "committed_at": "2026-06-09T00:00:00Z",
        },
        "license": {
            "spdx_id": "MIT",
            "name": "MIT License",
            "api_url": f"https://api.github.com/repos/{repo}/license",
            "html_url": f"https://github.com/{repo}/blob/main/LICENSE",
            "path": "LICENSE",
            "sha": "def456",
            "download_url": f"https://raw.githubusercontent.com/{repo}/main/LICENSE",
        },
        "repo_activity": {
            "updated_at": "2026-06-09T00:00:00Z",
            "pushed_at": "2026-06-09T00:00:00Z",
        },
        "skill_blob": {
            "path": f"{path}/SKILL.md",
            "sha": "skillsha",
            "url": f"https://api.github.com/repos/{repo}/git/blobs/skillsha",
        },
    }


def test_evaluate_bundle_marks_node_25_as_incompatible(monkeypatch):
    monkeypatch.setattr("tools.skills_intake_audit._repo_evidence_for_bundle", lambda *a, **k: _mock_source_evidence())
    bundle = _bundle(
        "acme/repo/node-skill",
        """---
name: node-skill
description: test node skill
platforms: [macos]
---
Body
""",
        **{
            "package.json": '{"name":"node-skill","engines":{"node":">=25"}}',
            "scripts/run.js": "console.log('hi')\n",
        },
    )

    report = evaluate_bundle(bundle)

    assert report["evaluation_status"] == "incompatible"
    assert "node_engine_fail_closed" in report["reason_codes"]
    assert report["compatibility"]["node_runtime"]["minimum_major"] == 25


def test_evaluate_bundle_marks_unbounded_node_as_review_needed(monkeypatch):
    monkeypatch.setattr("tools.skills_intake_audit._repo_evidence_for_bundle", lambda *a, **k: _mock_source_evidence())
    bundle = _bundle(
        "acme/repo/unbounded-node",
        """---
name: unbounded-node
description: test node skill
platforms: [macos]
---
Body
""",
        **{"scripts/run.mjs": "console.log('hi')\n"},
    )

    report = evaluate_bundle(bundle)

    assert report["evaluation_status"] == "review_needed"
    assert "node_runtime_unbounded" in report["reason_codes"]


def test_evaluate_bundle_ignores_template_javascript_without_runtime_evidence(monkeypatch):
    monkeypatch.setattr("tools.skills_intake_audit._repo_evidence_for_bundle", lambda *a, **k: _mock_source_evidence())
    bundle = _bundle(
        "acme/repo/template-js",
        """---
name: template-js
description: browser template only
platforms: [macos]
---
Body
""",
        **{"templates/generator_template.js": "console.log('template')\n"},
    )

    report = evaluate_bundle(bundle)

    assert report["evaluation_status"] == "compatible"
    assert "node_runtime_unbounded" not in report["reason_codes"]


def test_audit_candidates_discovers_repo_skills_and_reports_statuses(monkeypatch):
    monkeypatch.setattr("tools.skills_intake_audit._repo_evidence_for_bundle", lambda bundle, **kwargs: _mock_source_evidence("good/repo", bundle.name))
    bundle_map = {
        "good/repo/compatible-skill": _bundle(
            "good/repo/compatible-skill",
            """---
name: compatible-skill
description: safe markdown skill
platforms: [macos]
---
Use safely.
""",
        ),
        "good/repo/review-skill": _bundle(
            "good/repo/review-skill",
            """---
name: review-skill
description: requires setup
platforms: [macos]
prerequisites:
  env_vars: [__INTAKE_TEST_MISSING__]
---
Use safely.
""",
        ),
    }

    def fake_resolve(identifier, _sources):
        bundle = bundle_map.get(identifier)
        if bundle is None:
            return None, None, None
        return FakeMeta(bundle.name), bundle, DummySource()

    monkeypatch.setattr("tools.skills_intake_audit._resolve_source_meta_and_bundle", fake_resolve)

    report = audit_candidates(
        ["https://github.com/good/repo"],
        sources=[DummySource()],
        github_source=FakeGitHubSource(
            [
                {"type": "blob", "path": "compatible-skill/SKILL.md"},
                {"type": "blob", "path": "review-skill/SKILL.md"},
            ]
        ),
        max_skills_per_repo=5,
    )

    assert report["schema_version"] == "hermes_external_skill_intake_v1"
    assert report["candidate_count"] == 1
    result = report["results"][0]
    assert result["skill_discovery"]["discovered_count"] == 2
    assert result["skill_discovery"]["audited_count"] == 2
    assert result["evaluation_status"] == "review_needed"
    skill_statuses = {skill["name"]: skill["evaluation_status"] for skill in result["skills"]}
    assert skill_statuses == {
        "compatible-skill": "compatible",
        "review-skill": "review_needed",
    }


def test_audit_candidates_limits_direct_urls_to_url_source(monkeypatch):
    captured = {}

    def fake_resolve(identifier, sources):
        captured["identifier"] = identifier
        captured["sources"] = [src.source_id() for src in sources]
        return FakeMeta("url-skill"), SkillBundle(
            name="url-skill",
            files={
                "SKILL.md": """---
name: url-skill
description: direct url skill
platforms: [macos]
---
Body
""",
            },
            source="url",
            identifier=identifier,
            trust_level="community",
        ), DummySource("url")

    monkeypatch.setattr("tools.skills_intake_audit._resolve_source_meta_and_bundle", fake_resolve)

    report = audit_candidates(
        ["https://example.com/skills/url-skill/SKILL.md"],
        sources=[DummySource("github"), DummySource("url"), DummySource("skills-sh")],
    )

    assert captured["identifier"] == "https://example.com/skills/url-skill/SKILL.md"
    assert captured["sources"] == ["url"]
    assert report["results"][0]["evaluation_status"] == "compatible"


def test_audit_candidates_limits_github_urls_to_github_source(monkeypatch):
    captured = {}

    def fake_resolve(identifier, sources):
        captured["identifier"] = identifier
        captured["sources"] = [src.source_id() for src in sources]
        return FakeMeta("gh-skill"), _bundle(
            identifier,
            """---
name: gh-skill
description: github skill
platforms: [macos]
---
Body
""",
        ), DummySource("github")

    monkeypatch.setattr("tools.skills_intake_audit._resolve_source_meta_and_bundle", fake_resolve)
    monkeypatch.setattr("tools.skills_intake_audit._repo_evidence_for_bundle", lambda *a, **k: _mock_source_evidence("octo/repo", "skills/gh-skill"))

    report = audit_candidates(
        ["https://github.com/octo/repo/tree/main/skills/gh-skill"],
        sources=[DummySource("github"), DummySource("url"), DummySource("skills-sh")],
    )

    assert captured["identifier"] == "octo/repo/skills/gh-skill"
    assert captured["sources"] == ["github"]
    assert report["results"][0]["evaluation_status"] == "compatible"


def test_evaluate_bundle_requires_github_commit_and_license_evidence(monkeypatch):
    monkeypatch.setattr(
        "tools.skills_intake_audit._repo_evidence_for_bundle",
        lambda *a, **k: {
            "repo": "acme/repo",
            "path": "skill",
            "repo_url": "https://github.com/acme/repo",
            "skill_url": "https://github.com/acme/repo/tree/main/skill",
            "default_branch": "main",
            "head_commit": None,
            "license": None,
            "repo_activity": {"updated_at": None, "pushed_at": None},
            "skill_blob": None,
        },
    )
    bundle = _bundle(
        "acme/repo/evidence-skill",
        """---
name: evidence-skill
description: needs github evidence
platforms: [macos]
---
Body
""",
    )

    report = evaluate_bundle(bundle)

    assert report["evaluation_status"] == "review_needed"
    assert "missing_commit_evidence" in report["reason_codes"]
    assert "missing_license_evidence" in report["reason_codes"]
