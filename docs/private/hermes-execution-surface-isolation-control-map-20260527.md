# Hermes Execution-Surface Isolation Control Map (v1)

SSOT: `agent.execution_surface_controls.CONTROL_MAP_VERSION = hermes_execution_surface_controls_v1`.

Purpose: make browser/file/network-provider/plugin/cron/taskpool execution boundaries explicit and testable before any permission-enforcement code is broadened.

| Surface | Default boundary | Required evidence artifact | Failure classification |
|---|---|---|---|
| browser | http/https navigation only; local file schemes and bare filesystem paths are policy-blocked before browser launch | `browser_replay_bundle_v1` with `browser_qa_validation_v1` and `browser_proof_log_entry` | `policy_blocked`, `degraded_evidence`, `validation_success` |
| file | project/workspace files are allowed; credential, device, cross-profile, and sensitive system paths fail closed or require explicit approval path | tool result plus write-deny/sensitive-path classification and read-back when modifying project artifacts | `write_denied`, `sensitive_path_policy`, `cross_profile_guard`, `binary_or_oversize_read_guard` |
| terminal/process | commands run in resolved workdir/backend with approval gates for dangerous operations; background jobs require durable process tracking | terminal command, exit code, stdout/stderr excerpt, and process registry/session id for background work | `approval_required`, `command_failed`, `timeout`, `process_not_tracked` |
| provider calls | provider/model resolution must use configured router metadata and credential availability without leaking secrets into prompts or logs | provider probe/router status with redacted credentials and model/provider id | `provider_unavailable`, `auth_missing`, `quorum_degraded`, `truth_gate_unsupported` |
| plugins/skills | skills/plugins may request only profile-scoped, relative credential files under `HERMES_HOME`; absolute paths and traversal are rejected | skill/plugin manifest audit plus credential-file mount decision | `credential_path_rejected`, `plugin_disabled`, `manifest_invalid` |
| cron | cron jobs execute with declared delivery/toolsets/workdir; `no_agent=True` scripts use filename-only script references and logged outcomes | cron job row, scheduler log/output artifact, and `last_run`/`last_status` | `missed_schedule`, `script_path_invalid`, `delivery_failed`, `no_agent_guard_failed` |
| taskpool ingest | task candidates must carry lane/priority/dependency/proof metadata; advisory multimodel fields are normalized before dispatch | task note frontmatter, add/transition command evidence, `selfcheck`, `proof-check`, and `next` read-back | `mission_envelope_missing`, `evidence_unstructured`, `autorun_disabled`, `closeout_evidence_missing` |

## Smoke fixture evidence

Implemented in `tests/agent/test_execution_surface_controls.py`:

1. Allowed report generation: `taskpool_ingest/write_report` passes only with structured evidence such as `artifact:docs/private/hermes-execution-surface-isolation-control-map-20260527.md`.
2. Rejected out-of-scope access:
   - file write to `/etc/passwd` => `sensitive_path_policy`;
   - plugin credential mount `../../.ssh/id_rsa` => `credential_path_rejected`;
   - browser navigation to `file:///tmp/secret.txt` => `policy_blocked`.

## Taskpool ingest linkage

For taskpool candidates touching these surfaces, creation/closeout evidence must cite one of the structured prefixes accepted by proof governance (`artifact:`, `proof:`, or `command:`) and include the surface-specific artifact above. Generic `py_compile`, proof hashes, or global `selfcheck`/`proof-check` output are control-plane health signals only; they are not sufficient product evidence for implementation-class closeout.
