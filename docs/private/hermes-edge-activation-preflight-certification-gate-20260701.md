# DecisionPacket / ContinuationPacket — edge activation preflight certification

schema: hermes_decision_continuation_packet_v1
id: hermes-edge-activation-preflight-certification-gate-20260701
status: certified_read_only_fail_closed_preflight

## Objective

Continue the edge-runtime boundary work after `edge-activation-preflight` without falsely claiming certification. The next gate is certification of the read-only/fail-closed preflight slice, not real runtime/device/external activation.

## Completed evidence

1. Scoped implementation commit:
   - repo: `/Users/charles/.hermes/hermes-agent`
   - commit: `b8e7f9704 feat(eval): add edge activation preflight`
   - files:
     - `agent/reliability_harness.py`
     - `tests/agent/test_reliability_harness.py`
     - `skills/software-development/agent-eval-dynamic-workflow-harness/SKILL.md`
     - `docs/private/hermes-edge-activation-preflight-20260701.md`

2. Regression evidence:
   - command: `./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q`
   - result: `70 passed in 1.59s`

3. Static evidence:
   - command: `./venv/bin/python -m py_compile agent/reliability_harness.py tests/agent/test_reliability_harness.py && git diff --check -- ...`
   - result: exit 0, no output

4. Live blocked preflight evidence:
   - discovery schema: `hermes_edge_authority_discovery_v1`
   - discovery opening_ready: `false`
   - preflight schema: `hermes_edge_activation_preflight_v1`
   - preflight status: `blocked`
   - reason: `discovery_not_opening_ready`
   - runtime_effect: `read_only_preflight`
   - execute: `false`
   - blockers:
     - `exact_runtime_activation_packet`
     - `exact_runtime_activation_packet_execute_true`
     - `fix_benchmark_receipt_or_closed_gates`

5. Machine-readable certification packet:
   - `docs/private/hermes-edge-activation-preflight-certification-20260701.json`
   - status: `certified_read_only_fail_closed_preflight`
   - JSON lint: pass

6. Independent verifier vote:
   - delegation: `deleg_f7ce792b`
   - vote: `approve`
   - best_within_constraints: `true`
   - certified scope: commit `b8e7f9704` scoped slice only
   - certified as: read-only fail-closed preflight
   - certified no runtime/device/external boundaries opened: `true`

7. AcceptanceGovernance final readback:
   - schema: `hermes_acceptance_governance_result_v1`
   - status: `certified`
   - failures: `[]`
   - claim_count: `4`
   - evidence_count: `4`

## Pending action

No pending action remains for certification of commit `b8e7f9704` as a read-only/fail-closed preflight slice.

The next pending action is a separate human-gated real activation boundary, if desired. It is not authorized or certified by this packet.

Required future boundary before real activation:

```text
exact runtime/device/external target
fully bound execute=true activation packet
rollback/restore path
receipt path
post-action readback command/source
human/operator acknowledgement
separate executor boundary
```

## Requested authority

No additional runtime/device/external authority is requested or consumed for this certified slice.

The certification update consumed only local documentation authority for recording the independent verifier vote and AcceptanceGovernance result.

Future real activation remains separately gated and blocked until a new exact executor-boundary AuthorityGrant exists.

## Rollback / escape hatch

All current work is local-file only. Rollback is:

```bash
git restore --staged docs/private/hermes-edge-activation-preflight-certification-20260701.json docs/private/hermes-edge-activation-preflight-certification-gate-20260701.md
git restore docs/private/hermes-edge-activation-preflight-certification-20260701.json docs/private/hermes-edge-activation-preflight-certification-gate-20260701.md
rm -f docs/private/hermes-edge-activation-preflight-certification-20260701.json docs/private/hermes-edge-activation-preflight-certification-gate-20260701.md
```

No runtime/device/external side effect needs rollback because none was performed.

## Post-action verification

After verifier result is incorporated:

1. Validate JSON certification packet:
   ```bash
   ./venv/bin/python -m json.tool docs/private/hermes-edge-activation-preflight-certification-20260701.json >/tmp/hermes-edge-certification-jsonlint.out
   ```
2. Rerun regression:
   ```bash
   ./venv/bin/python -m pytest tests/agent/test_reliability_harness.py tests/agent/test_reliability_harness_distribution.py tests/test_trajectory_compressor.py tests/test_trajectory_compressor_async.py -q
   ```
3. Rerun live blocked preflight:
   ```bash
   ./venv/bin/python -m agent.reliability_harness edge-authority-discover --scan-dir /tmp --scan-dir /Users/charles/.hermes/hermes-agent/docs/private --json > /tmp/hermes-edge-cert-discovery.json
   ./venv/bin/python -m agent.reliability_harness edge-activation-preflight --discovery /tmp/hermes-edge-cert-discovery.json --target-id local-mac-fixture --json
   ```
4. Confirm boundary remains closed unless a later exact executor boundary is separately authorized.

## Certification state

Current state: certified for the read-only/fail-closed preflight slice only.

This certification does not certify real runtime/device/external activation. Live activation remains blocked by:

```text
exact_runtime_activation_packet
exact_runtime_activation_packet_execute_true
fix_benchmark_receipt_or_closed_gates
```
