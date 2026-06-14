from __future__ import annotations

from workflow_kernel.payload_guard import classify_payload
from workflow_kernel.target_registry import enabled_targets, registry_snapshot, validate_registry


def test_target_registry_opens_multi_target_plumbing_with_explicit_enabled_target() -> None:
    registry = registry_snapshot()

    assert registry["multi_target_enabled"] is True
    assert registry["arbitrary_target_discovery_enabled"] is False
    assert validate_registry(registry)["status"] == "PASS"
    targets = enabled_targets(registry)
    assert [t["target_id"] for t in targets] == ["telegram_home"]
    assert targets[0]["daily_limit"] == 1


def test_payload_guard_allows_enterprise_heartbeat_and_blocks_secret_like_payload() -> None:
    ok = classify_payload("Workflow Kernel enterprise heartbeat: all governed checks green.", purpose="workflow_kernel_enterprise_heartbeat")
    bad = classify_payload("token=SHOULD_NOT_SEND", purpose="workflow_kernel_enterprise_heartbeat")

    assert ok["status"] == "PASS"
    assert ok["dynamic_payload_allowed"] is True
    assert bad["status"] == "FAIL"
    assert "blocked_pattern:secret_like" in bad["errors"]
