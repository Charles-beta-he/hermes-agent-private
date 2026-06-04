from agent.ux9_contracts import (
    UX9_SSOT_PATH,
    DeviceTwinContract,
    DiagnoseContract,
    MultiModelGovernanceContract,
    TaskLedgerContract,
    ux9_contract_manifest,
)


def test_ux9_manifest_binds_every_contract_to_single_ssot():
    manifest = ux9_contract_manifest()

    assert manifest["schema"] == "ux9_verifier_contracts_v1"
    assert manifest["ssot_ref"] == UX9_SSOT_PATH
    assert set(manifest["contracts"]) == {
        "diagnose",
        "device_twin",
        "task_ledger",
        "multimodel_governance",
    }
    assert "No degraded advisory triggering high-risk execution." in manifest["invariants"]


def test_diagnose_contract_requires_structured_evidence_and_recovery_actions():
    contract = DiagnoseContract(
        reason_code="provider_rate_limited",
        recovery_actions=("retry_with_fallback", "surface_budget_warning"),
        risk_level="medium",
        evidence_refs=("command:consult_models status showed provider_rate_limited",),
    )

    assert contract.validate() == []
    payload = contract.to_dict()
    assert payload["ssot_ref"] == UX9_SSOT_PATH
    assert payload["recovery_actions"] == ["retry_with_fallback", "surface_budget_warning"]

    unstructured = DiagnoseContract(
        reason_code="provider_rate_limited",
        recovery_actions=("retry_with_fallback",),
        risk_level="medium",
        evidence_refs=("plain text evidence",),
    )
    assert "evidence_ref is not structured" in "\n".join(unstructured.validate())


def test_device_twin_contract_is_reported_only_boundary():
    contract = DeviceTwinContract(
        device_id="edge-local-1",
        reported_state={"battery": "charging"},
        observed_state={"last_seen_s": 3},
        verified_state={"diagnose": "pass"},
        trust_level="trusted_low",
        health="healthy",
        capabilities=("read_file",),
    )

    assert contract.validate() == []
    payload = contract.to_dict()
    assert "desired_state" not in payload
    assert "commands" not in payload
    assert payload["reported_state"] == {"battery": "charging"}


def test_task_ledger_contract_explains_transition_and_fencing():
    contract = TaskLedgerContract(
        task_id="task-1",
        from_status="running",
        to_status="review",
        reason_code="bounded_tranche_complete",
        attempt_id="attempt-1",
        lease_epoch=2,
        idempotency_key="task-1:attempt-1:review",
        evidence_refs=("artifact:/tmp/contract-test-output.json", "command:pytest tests/agent/test_ux9_contracts.py PASS"),
        recovery_proposal="requeue to pending if test artifact is missing",
    )

    assert contract.validate() == []
    assert contract.to_dict()["lease_epoch"] == 2

    invalid = TaskLedgerContract(
        task_id="task-1",
        from_status="running",
        to_status="review",
        reason_code="bounded_tranche_complete",
        attempt_id="attempt-1",
        lease_epoch=-1,
        idempotency_key="task-1:attempt-1:review",
        evidence_refs=("command:pytest PASS",),
    )
    assert "lease_epoch must be non-negative" in invalid.validate()


def test_multimodel_governance_degraded_outcomes_are_advisory_and_restricted():
    contract = MultiModelGovernanceContract(
        status="degraded",
        evidence_coverage="partial",
        model_disagreement="high",
        fallback_reason="min_quorum_not_met",
        action_restrictions=("no_auto_done", "no_high_risk_execution"),
        advisory_only=True,
    )

    assert contract.validate() == []

    unsafe = MultiModelGovernanceContract(
        status="degraded",
        evidence_coverage="partial",
        model_disagreement="high",
        advisory_only=False,
    )
    failures = "\n".join(unsafe.validate())
    assert "non-certified multimodel outcomes must remain advisory_only" in failures
    assert "non-certified multimodel outcomes require action_restrictions" in failures
