import json
from types import SimpleNamespace

from agent.system_prompt import build_system_prompt_content_blocks


def _agent(**overrides):
    base = dict(
        model="gpt-test",
        provider="test-provider",
        platform="telegram",
        valid_tool_names=["kanban_show", "skill_view", "terminal"],
        enabled_toolsets=["kanban", "terminal"],
        _cached_system_prompt_parts={
            "stable": "stable prompt with kanban guidance",
            "context": "project context",
            "volatile": "date and memory",
        },
        _cached_system_prompt="stable prompt with kanban guidance\n\nproject context\n\ndate and memory",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_prompt_manifest_v1_has_stable_schema_and_hashes_prompt_parts():
    from agent.prompt_manifest import build_prompt_manifest

    manifest = build_prompt_manifest(_agent(), source_files=["agent/system_prompt.py"])

    assert manifest["schema_version"] == "PromptManifestV1"
    assert manifest["prompt_id"] == "hermes-core-system-prompt"
    assert manifest["semantic_version"]
    assert len(manifest["content_hash"]) == 64
    assert len(manifest["manifest_hash"]) == 64
    assert len(manifest["skill_set_hash"]) == 64
    assert manifest["source_files"] == ["agent/system_prompt.py"]
    assert manifest["platform_profile"] == "telegram"
    assert manifest["toolset_profile"] == ["kanban", "terminal"]


def test_prompt_manifest_hash_changes_when_prompt_content_drifts():
    from agent.prompt_manifest import build_prompt_manifest, check_prompt_manifest_drift

    baseline = build_prompt_manifest(_agent())
    changed = build_prompt_manifest(_agent(_cached_system_prompt_parts={
        "stable": "stable prompt with changed kanban guidance",
        "context": "project context",
        "volatile": "date and memory",
    }))

    drift = check_prompt_manifest_drift(baseline, changed)

    assert drift["drifted"] is True
    assert drift["changed_fields"] == ["content_hash", "manifest_hash"]


def test_prompt_content_blocks_include_non_cacheable_manifest_block_without_breaking_cache_prefix():
    from agent.prompt_manifest import build_prompt_manifest

    agent = _agent()
    manifest = build_prompt_manifest(agent)
    blocks = build_system_prompt_content_blocks(
        agent,
        system_prompt=agent._cached_system_prompt,
        prompt_manifest=manifest,
    )

    assert blocks[:3] == [
        {"type": "text", "text": "stable prompt with kanban guidance", "_hermes_cacheable": True},
        {"type": "text", "text": "project context", "_hermes_cacheable": True},
        {"type": "text", "text": "date and memory", "_hermes_cacheable": False},
    ]
    assert blocks[3]["_hermes_cacheable"] is False
    assert json.loads(blocks[3]["text"].removeprefix("PromptManifestV1: "))["content_hash"] == manifest["content_hash"]


def test_prompt_content_blocks_auto_append_agent_manifest_without_breaking_cache_prefix():
    agent = _agent(_prompt_manifest_v1={
        "schema_version": "PromptManifestV1",
        "content_hash": "c" * 64,
        "manifest_hash": "d" * 64,
    })

    blocks = build_system_prompt_content_blocks(
        agent,
        system_prompt=agent._cached_system_prompt,
    )

    assert blocks[:3] == [
        {"type": "text", "text": "stable prompt with kanban guidance", "_hermes_cacheable": True},
        {"type": "text", "text": "project context", "_hermes_cacheable": True},
        {"type": "text", "text": "date and memory", "_hermes_cacheable": False},
    ]
    assert blocks[3]["_hermes_cacheable"] is False
    assert json.loads(blocks[3]["text"].removeprefix("PromptManifestV1: "))["content_hash"] == "c" * 64


def test_session_model_config_records_prompt_manifest_for_gateway_and_cron_paths():
    from agent.prompt_manifest import attach_prompt_manifest_metadata

    agent = _agent(_session_init_model_config={"max_iterations": 3})
    manifest = attach_prompt_manifest_metadata(agent)

    assert agent._session_init_model_config["prompt_hash"] == manifest["content_hash"]
    assert agent._session_init_model_config["prompt_manifest"]["manifest_hash"] == manifest["manifest_hash"]


def test_trajectory_metadata_records_prompt_manifest():
    from agent.prompt_manifest import attach_prompt_manifest_metadata, prompt_manifest_metadata

    agent = _agent()
    manifest = attach_prompt_manifest_metadata(agent)
    metadata = prompt_manifest_metadata(agent)

    assert metadata["prompt_hash"] == manifest["content_hash"]
    assert metadata["prompt_manifest"]["schema_version"] == "PromptManifestV1"


def test_trajectory_save_writes_prompt_manifest_metadata(tmp_path):
    from agent.trajectory import save_trajectory

    output = tmp_path / "trajectory.jsonl"
    metadata = {
        "prompt_hash": "e" * 64,
        "prompt_manifest": {"schema_version": "PromptManifestV1", "content_hash": "e" * 64},
    }

    save_trajectory([{"from": "human", "value": "hi"}], "gpt-test", True, filename=str(output), metadata=metadata)

    entry = json.loads(output.read_text(encoding="utf-8"))
    assert entry["metadata"]["prompt_hash"] == "e" * 64
    assert entry["metadata"]["prompt_manifest"]["schema_version"] == "PromptManifestV1"


def test_kanban_completion_metadata_merges_prompt_manifest_env(monkeypatch):
    from tools.kanban_tools import _merge_prompt_manifest_metadata

    monkeypatch.setenv("HERMES_PROMPT_MANIFEST_JSON", json.dumps({
        "schema_version": "PromptManifestV1",
        "content_hash": "a" * 64,
        "manifest_hash": "b" * 64,
    }))

    merged = _merge_prompt_manifest_metadata({"changed_files": ["x"]})

    assert merged["changed_files"] == ["x"]
    assert merged["prompt_hash"] == "a" * 64
    assert merged["prompt_manifest"]["manifest_hash"] == "b" * 64
