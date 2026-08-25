from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent.prompt_ledger as prompt_ledger
from agent.prompt_ledger import (
    call_with_prompt_ledger,
    measure_request,
    project_messages_shadow,
)
from scripts.replay_prompt_ledger_shadow import replay_sessions


FIXTURE_ROOT = Path(__file__).parents[1] / "fixtures"


def test_prompt_ledger_v1_source_manifest_and_shadow_coverage_contract():
    from agent.prompt_manifest import (
        build_prompt_source_manifest,
        check_prompt_source_drift,
    )
    from agent.prompt_ledger import (
        append_prompt_ledger_record,
        build_prompt_ledger_record,
        canonical_prompt_ledger_bytes,
        evaluate_shadow_context_coverage,
        export_prompt_ledger,
    )

    fixture = json.loads(
        (FIXTURE_ROOT / "prompt" / "prompt-ledger-synthetic.json").read_text()
    )
    source_manifest = build_prompt_source_manifest(fixture["prompt_sources"])
    assert source_manifest["sources"][0] == {
        "source_id": "prompt-source-sha256:" + "a" * 64,
        "content_hash": "a" * 64,
        "provenance": "repository",
        "authority": "system",
        "certification_partition": "certified/stable:v1",
    }
    assert source_manifest == build_prompt_source_manifest(
        [dict(reversed(tuple(item.items()))) for item in fixture["prompt_sources"]]
    )

    changed_sources = copy.deepcopy(fixture["prompt_sources"])
    changed_sources[0].update(
        content_hash="e" * 64,
        provenance="generated",
        authority="untrusted",
        certification_partition="CERTIFIED/stable:v1",
    )
    drift = check_prompt_source_drift(
        source_manifest,
        build_prompt_source_manifest(changed_sources),
    )
    assert drift["structural_drift_reasons"] == [
        "content_hash_changed",
        "provenance_changed",
        "authority_changed",
        "certification_partition_changed",
    ]

    records = []
    for raw in fixture["records"]:
        records = append_prompt_ledger_record(records, build_prompt_ledger_record(raw))
    report = export_prompt_ledger(records)

    assert [record["call_class"] for record in report["records"]] == [
        "user_response",
        "fallback",
    ]
    assert report["records"][1]["usage"] == {
        "fresh_input_tokens": None,
        "cache_read_tokens": None,
        "cache_write_tokens": None,
        "output_tokens": 4,
        "reasoning_tokens": 2,
    }
    assert report["aggregate"]["component_counts"]["stable_prompt_tokens"] == 150
    assert report["aggregate"]["usage"]["fresh_input_tokens"] == 40
    assert report["aggregate"]["usage_unknown_record_counts"] == {
        "fresh_input_tokens": 1,
        "cache_read_tokens": 1,
        "cache_write_tokens": 1,
        "output_tokens": 0,
        "reasoning_tokens": 0,
    }
    assert report["aggregate"]["total_tokens"] is None
    assert report["models"][0]["total_tokens"] == 125
    assert report["models"][0]["usage"]["reasoning_tokens"] == 7
    assert report["models"][1]["total_tokens"] is None
    assert {row["provenance_ref"] for row in report["provenances"]} == {
        "provenance:core",
        "provenance:extension",
    }
    assert {row["source_ref"] for row in report["sources"]} == {
        "source:cli",
        "source:gateway",
    }
    assert canonical_prompt_ledger_bytes(report) == canonical_prompt_ledger_bytes(
        export_prompt_ledger(records)
    )

    coverage_metadata = json.loads(
        (FIXTURE_ROOT / "prompt" / "context-coverage-synthetic.json").read_text()
    )
    disabled = evaluate_shadow_context_coverage(coverage_metadata)
    assert disabled == {
        "schema_version": "ShadowContextCoverageV1",
        "enabled": False,
        "evaluated": False,
        "advisory": True,
        "fail_closed": True,
        "dispatch_allowed": False,
    }
    coverage = evaluate_shadow_context_coverage(coverage_metadata, enabled=True)
    assert coverage["coverage"]["human_gates"] is False
    assert coverage["missing"] == ["human_gates"]
    assert coverage["complete"] is False
    assert coverage["advisory"] is True
    assert coverage["fail_closed"] is True
    assert coverage["dispatch_allowed"] is False
    assert coverage == evaluate_shadow_context_coverage(coverage_metadata, enabled=True)

    forbidden = ("sk-synthetic-secret", "chat-target-", "/users/charles", "/home/")
    durable = canonical_prompt_ledger_bytes(
        {"manifest": source_manifest, "ledger": report, "coverage": coverage}
    ).decode("utf-8")
    assert all(term not in durable.lower() for term in forbidden)


def _ledger_for(root: Path, session_id: str) -> Path:
    return prompt_ledger._ledger_path(SimpleNamespace(session_id=session_id), root)


def _multimodal_tool(text: str, image_url: str, *, call_id: str) -> dict:
    return {
        "role": "tool",
        "tool_call_id": call_id,
        "tool_name": "vision_analyze",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ],
    }


def test_measure_request_counts_decoded_nul_json_and_media_bytes():
    encoded = "\x00json:" + json.dumps(
        [
            {"type": "text", "text": "seen"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,aGVsbG8="},
            },
        ]
    )
    metrics = measure_request({"messages": [{"role": "tool", "content": encoded}]})

    assert metrics["multimodal"]["parts"] == 1
    assert metrics["multimodal"]["decoded_media_bytes"] == 5
    assert metrics["messages"]["serialized_chars"] > len("\x00json:")


def test_projection_is_non_mutating_and_protects_latest_multimodal_result(tmp_path):
    old_image = tmp_path / "old.png"
    new_image = tmp_path / "new.png"
    old_image.write_bytes(b"old")
    new_image.write_bytes(b"new")
    messages = [
        {"role": "system", "content": "stable"},
        _multimodal_tool("old observation " + "x" * 2000, old_image.as_uri(), call_id="old"),
        {"role": "assistant", "content": "I consumed the old image"},
        _multimodal_tool("new observation", new_image.as_uri(), call_id="new"),
    ]
    original = copy.deepcopy(messages)

    report = project_messages_shadow(messages, protect_last_tool_results=1)

    assert messages == original
    assert report["projected_messages"][-1] == messages[-1]
    assert report["candidates"][0]["tool_call_id"] == "old"
    assert report["candidates"][0]["kind"] == "multimodal_tool_result"
    assert report["candidates"][0]["recoverable"] is True
    assert report["projected_chars"] < report["original_chars"]
    envelope = json.loads(report["projected_messages"][1]["content"])
    assert envelope["reload_ref"] == [
        {
            "url": old_image.as_uri(),
            "sha256": hashlib.sha256(old_image.read_bytes()).hexdigest(),
        }
    ]


def test_remote_multimodal_reference_is_not_claimed_recoverable():
    messages = [
        _multimodal_tool(
            "unverified remote " + "x" * 5000,
            "https://127.0.0.1:1/nonexistent.png",
            call_id="remote",
        ),
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["would_apply"] is False
    assert report["candidates"][0]["retirement_blocker"] == "remote_reference_unverified"
    assert report["projected_messages"] == messages


def test_oversized_local_multimodal_artifact_is_not_hashed_or_projected(tmp_path):
    image = tmp_path / "oversized.png"
    with image.open("wb") as handle:
        handle.truncate(prompt_ledger._MAX_ARTIFACT_BYTES + 1)
    messages = [
        _multimodal_tool("oversized " + "x" * 5000, image.as_uri(), call_id="oversized"),
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["would_apply"] is False
    assert report["candidates"][0]["retirement_blocker"] == "unsafe_artifact"
    assert report["projected_messages"] == messages


def test_projection_never_retires_user_images():
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "inspect this"},
                {"type": "image_url", "image_url": {"url": "file:///tmp/user.png"}},
            ],
        }
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0)

    assert report["projected_messages"] == messages
    assert report["candidates"] == []


def test_missing_multimodal_artifact_is_reported_but_not_projected():
    messages = [
        _multimodal_tool(
            "inline image",
            "data:image/png;base64,aGVsbG8=",
            call_id="inline",
        ),
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["would_apply"] is False
    assert report["candidates"][0]["retirement_blocker"] == "inline_media"
    assert report["projected_messages"] == messages


def test_multimodal_projection_requires_every_media_part_to_be_recoverable(tmp_path):
    image = tmp_path / "kept.png"
    image.write_bytes(b"image")
    message = _multimodal_tool("mixed media " + "x" * 2000, image.as_uri(), call_id="mixed")
    message["content"].append(
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,aGVsbG8="}}
    )
    messages = [message, {"role": "assistant", "content": "consumed"}]

    report = project_messages_shadow(messages, protect_last_tool_results=0)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["retirement_blocker"] == "inline_media"
    assert report["projected_messages"] == messages


def test_old_persisted_text_tool_result_gets_bounded_redacted_envelope(tmp_path):
    persisted = tmp_path / "result.txt"
    persisted.write_text("full output")
    secret = "sk-" + "x" * 32
    content = (
        "<persisted-output>\n"
        "This tool result was too large (5,100 characters, 5.0 KB).\n"
        f"Full output saved to: {persisted}\n"
        f"Raw UTF-8 SHA-256 (source): {hashlib.sha256(persisted.read_bytes()).hexdigest()}\n\n"
        "Preview:\n"
        f"token={secret}\n"
        + "x" * 5000
        + "\n</persisted-output>"
    )
    messages = [
        {"role": "tool", "tool_name": "terminal", "tool_call_id": "t1", "content": content},
        {"role": "assistant", "content": "used it"},
    ]

    report = project_messages_shadow(
        messages,
        protect_last_tool_results=0,
        min_text_chars=1000,
        preview_chars=1000,
    )
    envelope = report["projected_messages"][0]["content"]

    assert report["candidates"][0]["recoverable"] is True
    assert report["candidates"][0]["would_apply"] is True
    assert secret not in envelope
    assert "token=[REDACTED]" in envelope
    assert len(envelope) < 2000


def test_persisted_text_hash_mismatch_falls_back_to_full_context(tmp_path):
    artifact = tmp_path / "result.txt"
    artifact.write_text("actual bytes", encoding="utf-8")
    content = (
        "<persisted-output>\n"
        f"Full output saved to: {artifact}\n"
        + "Raw UTF-8 SHA-256 (source): "
        + hashlib.sha256(b"different bytes").hexdigest()
        + "\n\n"
        + "x" * 5000
        + "\n</persisted-output>"
    )
    messages = [
        {"role": "tool", "tool_call_id": "t1", "content": content},
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0, min_text_chars=100)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["would_apply"] is False
    assert report["candidates"][0]["retirement_blocker"] == "source_hash_mismatch"
    assert report["projected_messages"] == messages


def test_persisted_text_symlink_reference_falls_back_to_full_context(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("actual bytes", encoding="utf-8")
    artifact = tmp_path / "artifact.txt"
    artifact.symlink_to(target)
    content = (
        "<persisted-output>\n"
        f"Full output saved to: {artifact}\n"
        f"Raw UTF-8 SHA-256 (source): {hashlib.sha256(target.read_bytes()).hexdigest()}\n\n"
        + "x" * 5000
        + "\n</persisted-output>"
    )
    messages = [
        {"role": "tool", "tool_call_id": "t1", "content": content},
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0, min_text_chars=100)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["retirement_blocker"] == "unsafe_artifact"
    assert report["projected_messages"] == messages


def test_persisted_text_symlink_ancestor_falls_back_to_full_context(tmp_path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    target = real_dir / "artifact.txt"
    target.write_text("actual bytes", encoding="utf-8")
    linked_dir = tmp_path / "linked"
    linked_dir.symlink_to(real_dir, target_is_directory=True)
    content = (
        "<persisted-output>\n"
        f"Full output saved to: {linked_dir / 'artifact.txt'}\n"
        f"Raw UTF-8 SHA-256 (source): {hashlib.sha256(target.read_bytes()).hexdigest()}\n\n"
        + "x" * 5000
        + "\n</persisted-output>"
    )
    messages = [
        {"role": "tool", "tool_call_id": "t1", "content": content},
        {"role": "assistant", "content": "consumed"},
    ]

    report = project_messages_shadow(messages, protect_last_tool_results=0, min_text_chars=100)

    assert report["candidates"][0]["recoverable"] is False
    assert report["candidates"][0]["would_apply"] is False
    assert report["candidates"][0]["retirement_blocker"] == "artifact_read_error"
    assert report["projected_messages"] == messages


def test_responses_input_function_output_is_measured_and_projected(tmp_path):
    artifact = tmp_path / "response-output.txt"
    artifact.write_text("full responses output", encoding="utf-8")
    persisted = (
        "<persisted-output>\n"
        f"Full output saved to: {artifact}\n"
        'Read it with read_file(path="...").\n'
        f"Raw UTF-8 SHA-256 (source): {hashlib.sha256(artifact.read_bytes()).hexdigest()}\n\n"
        + "x" * 5000
        + "\n</persisted-output>"
    )
    items = [
        {"type": "function_call_output", "call_id": "call_1", "output": persisted},
        {"role": "assistant", "content": "consumed"},
    ]
    payload = {"input": items, "instructions": "system", "tools": []}
    before = copy.deepcopy(payload)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=10,
            output_tokens=2,
            input_tokens_details=SimpleNamespace(cached_tokens=0, cache_creation_tokens=0),
        )
    )

    assert measure_request(payload)["tool_results"]["count"] == 1
    call_with_prompt_ledger(
        SimpleNamespace(
            session_id="responses",
            provider="openai-codex",
            model="gpt-test",
            api_mode="codex_responses",
        ),
        payload,
        lambda current: response,
        config={
            "context_projection": {
                "enabled": True,
                "mode": "shadow",
                "protect_last_tool_results": 0,
                "min_text_chars": 100,
            }
        },
        ledger_dir=tmp_path,
    )

    assert payload == before
    rows = [
        json.loads(line)
        for line in _ledger_for(tmp_path, "responses").read_text(encoding="utf-8").splitlines()
    ]
    assert rows[0]["projection"]["candidate_count"] == 1
    assert rows[0]["projection"]["recoverable_candidate_count"] == 1


def test_durable_ledger_bounds_and_redacts_candidate_metadata(tmp_path):
    secret = "sk-" + "s" * 32
    private_material = "BEGIN_PRIVATE_MATERIAL_ABCDEF123456"
    messages = [
        {
            "role": "tool",
            "tool_name": f"privateKey={private_material}",
            "tool_call_id": secret + "y" * 500,
            "content": [
                {"type": "text", "text": "result"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,aA=="}},
            ],
        }
        for _ in range(105)
    ]
    messages.append({"role": "assistant", "content": "consumed"})

    call_with_prompt_ledger(
        SimpleNamespace(
            session_id="bounded",
            provider=f"privateKey={private_material}",
            model=f"model-{secret}",
            api_mode="chat_completions",
        ),
        {"messages": messages},
        lambda _payload: SimpleNamespace(usage=None),
        config={
            "context_projection": {
                "enabled": True,
                "mode": "shadow",
                "protect_last_tool_results": 0,
            }
        },
        ledger_dir=tmp_path,
    )

    raw = _ledger_for(tmp_path, "bounded").read_text(encoding="utf-8")
    request = json.loads(raw.splitlines()[0])
    projection = request["projection"]
    assert projection["candidate_count"] == 105
    assert projection["candidate_records_truncated"] == 5
    assert len(projection["candidates"]) == 100
    assert secret not in raw
    assert private_material not in raw
    assert "privateKey" not in raw
    assert "y" * 200 not in raw
    assert all(len(item["tool_call_id_sha256"]) == 64 for item in projection["candidates"])
    assert all(len(item["tool_name_sha256"]) == 64 for item in projection["candidates"])
    assert len(request["session_id_sha256"]) == 64
    assert len(request["provider_sha256"]) == 64
    assert len(request["model_sha256"]) == 64


def test_config_file_exact_gate_enables_shadow_without_runtime_config_hooks(tmp_path, monkeypatch):
    home = tmp_path / "hermes-home"
    home.mkdir(mode=0o700)
    (home / "config.yaml").write_text(
        "context_projection:\n  enabled: true\n  mode: shadow\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(prompt_ledger, "get_hermes_home", lambda: home)
    prompt_ledger._CONFIG_CACHE.clear()

    response = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2, total_tokens=7)
    )
    result = call_with_prompt_ledger(
        SimpleNamespace(session_id="s1", provider="p", model="m", api_mode="chat_completions"),
        {"messages": [{"role": "user", "content": "hello"}]},
        lambda payload: response,
    )

    assert result is response
    ledger = _ledger_for(home / "prompt_ledgers", "s1")
    assert ledger.exists()
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_call_wrapper_is_disabled_by_default_and_preserves_object_identity(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    seen = []

    result = call_with_prompt_ledger(
        agent,
        payload,
        lambda value: seen.append(value) or "response",
        config={},
        ledger_dir=tmp_path,
    )

    assert result == "response"
    assert seen == [payload]
    assert seen[0] is payload
    assert list(tmp_path.glob("*.jsonl")) == []


def test_call_wrapper_records_request_and_provider_usage_without_mutating_payload(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {
        "messages": [
            {"role": "user", "content": "hello"},
            {"role": "tool", "tool_call_id": "t1", "content": "x" * 100},
        ],
        "tools": [{"type": "function", "function": {"name": "terminal"}}],
    }
    original = copy.deepcopy(payload)
    response = SimpleNamespace(
        usage=SimpleNamespace(
            prompt_tokens=123,
            completion_tokens=7,
            total_tokens=130,
            prompt_tokens_details=SimpleNamespace(cached_tokens=80),
        )
    )

    result = call_with_prompt_ledger(
        agent,
        payload,
        lambda value: response,
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=tmp_path,
    )

    assert result is response
    assert payload == original
    rows = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert [row["event"] for row in rows] == ["request", "response"]
    assert rows[0]["projection_mode"] == "shadow"
    assert rows[0]["request_metrics"]["tools"]["count"] == 1
    assert rows[1]["provider_usage"] == {
        "prompt_tokens": 123,
        "completion_tokens": 7,
        "total_tokens": 130,
        "input_tokens": 43,
        "output_tokens": 7,
        "cache_read_tokens": 80,
        "cache_write_tokens": 0,
        "reasoning_tokens": 0,
    }


def test_call_wrapper_normalizes_responses_api_usage(tmp_path):
    agent = SimpleNamespace(
        session_id="s1",
        provider="openai-codex",
        model="gpt-test",
        api_mode="codex_responses",
    )
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            total_tokens=120,
            input_tokens_details=SimpleNamespace(cached_tokens=60, cache_creation_tokens=5),
            output_tokens_details=SimpleNamespace(reasoning_tokens=12),
        )
    )

    call_with_prompt_ledger(
        agent,
        {"input": [{"role": "user", "content": "hello"}]},
        lambda value: response,
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=tmp_path,
    )

    rows = [json.loads(line) for line in next(tmp_path.glob("*.jsonl")).read_text().splitlines()]
    assert rows[1]["provider_usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "total_tokens": 120,
        "input_tokens": 35,
        "output_tokens": 20,
        "cache_read_tokens": 60,
        "cache_write_tokens": 5,
        "reasoning_tokens": 12,
    }


def test_ledger_write_failure_is_fail_open_and_provider_exception_is_preserved(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    config = {"context_projection": {"enabled": True, "mode": "shadow"}}
    success_calls = []
    failure_calls = 0

    def failing_provider(_value):
        nonlocal failure_calls
        failure_calls += 1
        raise RuntimeError("provider failed")

    with patch("agent.prompt_ledger._append_jsonl", side_effect=OSError("disk full")):
        assert call_with_prompt_ledger(
            agent,
            payload,
            lambda value: success_calls.append(value) or "ok",
            config=config,
            ledger_dir=tmp_path,
        ) == "ok"
        with pytest.raises(RuntimeError, match="provider failed"):
            call_with_prompt_ledger(
                agent,
                payload,
                failing_provider,
                config=config,
                ledger_dir=tmp_path,
            )

    assert success_calls == [payload]
    assert failure_calls == 1


def test_provider_exception_survives_baseexception_from_error_ledger(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    config = {"context_projection": {"enabled": True, "mode": "shadow"}}
    provider_error = RuntimeError("original provider failure")
    provider_calls = 0
    append_calls = 0

    def provider(_value):
        nonlocal provider_calls
        provider_calls += 1
        raise provider_error

    def append_then_interrupt(_path, _record):
        nonlocal append_calls
        append_calls += 1
        if append_calls == 2:
            raise KeyboardInterrupt("ledger interruption")

    with patch("agent.prompt_ledger._append_jsonl", side_effect=append_then_interrupt):
        with pytest.raises(RuntimeError) as captured:
            call_with_prompt_ledger(
                agent,
                payload,
                provider,
                config=config,
                ledger_dir=tmp_path,
            )

    assert captured.value is provider_error
    assert provider_calls == 1
    assert append_calls == 2


def test_invalid_config_or_projection_failure_is_fail_open(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    invalid = {
        "context_projection": {
            "enabled": True,
            "mode": "shadow",
            "protect_last_tool_results": "not-an-int",
        }
    }

    calls = []
    assert call_with_prompt_ledger(
        agent,
        payload,
        lambda value: calls.append(("invalid", value)) or "invalid-config-still-called",
        config=invalid,
        ledger_dir=tmp_path,
    ) == "invalid-config-still-called"

    enabled = {"context_projection": {"enabled": True, "mode": "shadow"}}
    with patch("agent.prompt_ledger.project_messages_shadow", side_effect=ValueError("bad projection")):
        assert call_with_prompt_ledger(
            agent,
            payload,
            lambda value: calls.append(("projection", value)) or "projection-failure-still-called",
            config=enabled,
            ledger_dir=tmp_path,
        ) == "projection-failure-still-called"

    assert calls == [("invalid", payload), ("projection", payload)]


def test_disabled_wrapper_preserves_provider_exception_and_calls_once(tmp_path):
    class ProviderFailure(RuntimeError):
        pass

    calls = 0

    def provider(_payload):
        nonlocal calls
        calls += 1
        raise ProviderFailure("disabled-path-failure")

    with pytest.raises(ProviderFailure, match="disabled-path-failure"):
        call_with_prompt_ledger(
            SimpleNamespace(session_id="s1", provider="p", model="m"),
            {"messages": [{"role": "user", "content": "hello"}]},
            provider,
            config={},
            ledger_dir=tmp_path,
        )

    assert calls == 1
    assert list(tmp_path.glob("*.jsonl")) == []


def test_string_enabled_flag_fails_closed_without_writing(tmp_path):
    agent = SimpleNamespace(session_id="s1", provider="p", model="m")
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    calls = []

    assert call_with_prompt_ledger(
        agent,
        payload,
        lambda value: calls.append(value) or "ok",
        config={"context_projection": {"enabled": "true", "mode": "shadow"}},
        ledger_dir=tmp_path,
    ) == "ok"
    assert calls == [payload]
    assert list(tmp_path.glob("*.jsonl")) == []


def test_private_ledger_rejects_permissive_existing_file_fail_open(tmp_path):
    ledger = _ledger_for(tmp_path, "s1")
    ledger.write_text("original\n")
    ledger.chmod(0o644)
    before_mode = ledger.stat().st_mode & 0o777
    calls = []

    result = call_with_prompt_ledger(
        SimpleNamespace(session_id="s1", provider="p", model="m"),
        {"messages": []},
        lambda value: calls.append(value) or "ok",
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=tmp_path,
    )

    assert result == "ok"
    assert len(calls) == 1
    assert ledger.read_text() == "original\n"
    assert ledger.stat().st_mode & 0o777 == before_mode


def test_private_ledger_rejects_symlink_without_touching_target(tmp_path):
    target = tmp_path / "unrelated.txt"
    target.write_text("do not touch\n")
    target.chmod(0o644)
    target_mode = target.stat().st_mode & 0o777
    _ledger_for(tmp_path, "s1").symlink_to(target)
    calls = []

    result = call_with_prompt_ledger(
        SimpleNamespace(session_id="s1", provider="p", model="m"),
        {"messages": []},
        lambda value: calls.append(value) or "ok",
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=tmp_path,
    )

    assert result == "ok"
    assert len(calls) == 1
    assert target.read_text() == "do not touch\n"
    assert target.stat().st_mode & 0o777 == target_mode


def test_private_ledger_fifo_cannot_block_provider_dispatch(tmp_path):
    fifo = _ledger_for(tmp_path, "fifo")
    os.mkfifo(fifo, 0o600)
    script = f"""
from pathlib import Path
from types import SimpleNamespace
from agent.prompt_ledger import call_with_prompt_ledger
agent = SimpleNamespace(session_id='fifo', provider='p', model='m', api_mode='chat_completions')
result = call_with_prompt_ledger(
    agent,
    {{'messages': [{{'role': 'user', 'content': 'hello'}}]}},
    lambda payload: 'provider-called',
    config={{'context_projection': {{'enabled': True, 'mode': 'shadow'}}}},
    ledger_dir=Path({str(tmp_path)!r}),
)
print(result)
"""

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=2,
        check=True,
    )

    assert completed.stdout.strip() == "provider-called"


def test_private_ledger_lock_contention_is_bounded_and_provider_still_dispatches(tmp_path):
    if prompt_ledger.fcntl is None:
        pytest.skip("fcntl unavailable")
    ledger = _ledger_for(tmp_path, "locked")
    ledger.write_text("", encoding="utf-8")
    ledger.chmod(0o600)
    holder = os.open(ledger, os.O_WRONLY | os.O_APPEND)
    prompt_ledger.fcntl.flock(holder, prompt_ledger.fcntl.LOCK_EX | prompt_ledger.fcntl.LOCK_NB)
    calls: list[dict] = []
    agent = SimpleNamespace(
        session_id="locked",
        provider="p",
        model="m",
        api_mode="chat_completions",
    )
    payload = {"messages": [{"role": "user", "content": "hello"}]}
    started = time.monotonic()
    try:
        result = call_with_prompt_ledger(
            agent,
            payload,
            lambda value: calls.append(value) or "provider-called",
            config={"context_projection": {"enabled": True, "mode": "shadow"}},
            ledger_dir=tmp_path,
        )
    finally:
        prompt_ledger.fcntl.flock(holder, prompt_ledger.fcntl.LOCK_UN)
        os.close(holder)
    elapsed = time.monotonic() - started

    assert result == "provider-called"
    assert calls == [payload]
    assert elapsed < 0.5


def test_private_ledger_rejects_symlinked_ancestor_without_writing_target(tmp_path):
    target_root = tmp_path / "target-root"
    target_root.mkdir(mode=0o700)
    linked_root = tmp_path / "linked-root"
    linked_root.symlink_to(target_root, target_is_directory=True)
    ledger_dir = linked_root / "nested"
    calls = []

    result = call_with_prompt_ledger(
        SimpleNamespace(session_id="s1", provider="p", model="m"),
        {"messages": [{"role": "user", "content": "hello"}]},
        lambda payload: calls.append(payload) or "ok",
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=ledger_dir,
    )

    assert result == "ok"
    assert len(calls) == 1
    assert not _ledger_for(target_root / "nested", "s1").exists()


def test_private_ledger_rejects_existing_hard_link_without_touching_target(tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("do not touch\n", encoding="utf-8")
    victim.chmod(0o600)
    ledger_dir = tmp_path / "ledgers"
    ledger_dir.mkdir(mode=0o700)
    try:
        os.link(victim, _ledger_for(ledger_dir, "s1"))
    except OSError as exc:
        pytest.skip(f"hard links unavailable: {exc}")
    calls = []

    result = call_with_prompt_ledger(
        SimpleNamespace(session_id="s1", provider="p", model="m"),
        {"messages": [{"role": "user", "content": "hello"}]},
        lambda payload: calls.append(payload) or "ok",
        config={"context_projection": {"enabled": True, "mode": "shadow"}},
        ledger_dir=ledger_dir,
    )

    assert result == "ok"
    assert len(calls) == 1
    assert victim.read_text(encoding="utf-8") == "do not touch\n"
    assert victim.stat().st_nlink == 2


def test_private_ledger_concurrent_appends_remain_valid_jsonl(tmp_path):
    config = {"context_projection": {"enabled": True, "mode": "shadow"}}
    ledger_dir = tmp_path / "new-ledger-parent" / "nested"
    assert not ledger_dir.exists()
    agent = SimpleNamespace(
        session_id="concurrent",
        provider="p",
        model="m",
        api_mode="chat_completions",
    )

    def invoke(index):
        return call_with_prompt_ledger(
            agent,
            {"messages": [{"role": "user", "content": f"request-{index}"}]},
            lambda _payload: SimpleNamespace(usage=None),
            config=config,
            ledger_dir=ledger_dir,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(invoke, range(20)))

    assert len(results) == 20
    ledger = _ledger_for(ledger_dir, "concurrent")
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 40
    by_request = {}
    for row in rows:
        by_request.setdefault(row["request_id"], []).append(row["event"])
    assert len(by_request) == 20
    assert all(sorted(events) == ["request", "response"] for events in by_request.values())
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600


def test_read_only_replay_decodes_messages_and_does_not_modify_source_db(tmp_path):
    db_path = tmp_path / "state.db"
    artifact = tmp_path / "result.txt"
    artifact.write_text("full output")
    persisted = (
        "<persisted-output>\n"
        "This tool result was too large (6,000 characters, 5.9 KB).\n"
        f"Full output saved to: {artifact}\n"
        f"Raw UTF-8 SHA-256 (source): {hashlib.sha256(artifact.read_bytes()).hexdigest()}\n\n"
        "Preview:\n" + "x" * 6000 + "\n</persisted-output>"
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE sessions (id TEXT PRIMARY KEY);
            CREATE TABLE messages (
                id INTEGER PRIMARY KEY,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                tool_name TEXT,
                tool_call_id TEXT,
                active INTEGER NOT NULL DEFAULT 1
            );
            INSERT INTO sessions(id) VALUES ('s1');
            """
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content, tool_name, tool_call_id) VALUES (?, ?, ?, ?, ?)",
            ("s1", "tool", persisted, "terminal", "t1"),
        )
        conn.execute(
            "INSERT INTO messages(session_id, role, content) VALUES (?, ?, ?)",
            ("s1", "assistant", "consumed"),
        )

    before = db_path.stat()
    report = replay_sessions(
        db_path,
        session_ids=["s1"],
        top=1,
        protect_last_tool_results=0,
        min_text_chars=1000,
        preview_chars=100,
    )
    after = db_path.stat()

    assert before.st_mtime_ns == after.st_mtime_ns
    assert before.st_size == after.st_size
    assert report["source_open_mode"] == "read_only"
    assert report["source_integrity_scope"] == "main_file_stat_only"
    assert report["source_sidecars_checked"] is False
    assert report["source_main_file_metadata_unchanged"] is True
    assert report["sessions"][0]["session_id"] == "s1"
    assert report["sessions"][0]["estimated_reduction_chars"] > 0
    assert report["sessions"][0]["recoverable_candidate_count"] == 1
