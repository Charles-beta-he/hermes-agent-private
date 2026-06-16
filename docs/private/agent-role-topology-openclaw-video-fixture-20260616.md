# Agent Role Topology v1 — OpenClaw/Niuma Video-Derived Fixture

Date: 2026-06-16

## Source verification

Verified public source:

```json
{
  "html_url": "https://github.com/openclaw/openclaw",
  "full_name": "openclaw/openclaw",
  "description": "Your own personal AI assistant. Any OS. Any Platform. The lobster way. 🦞 ",
  "language": "TypeScript",
  "stars": 378922,
  "updated_at": "2026-06-16T06:18:03Z"
}
```

Source command:

```bash
python3 - <<'PY'
import json, urllib.request
url='https://api.github.com/repos/openclaw/openclaw'
with urllib.request.urlopen(url, timeout=20) as r:
    d=json.load(r)
print(json.dumps({'html_url':d.get('html_url'),'full_name':d.get('full_name'),'description':d.get('description'),'language':d.get('language'),'stars':d.get('stargazers_count'),'updated_at':d.get('updated_at')}, ensure_ascii=False, indent=2))
PY
```

Unverified / video-only:

- `Niuma` public source artifact was not verified in the available evidence.
- 140k LOC / 4-day build time remains a media-poster claim and is not accepted as implementation evidence.

## Landed fixture

File:

```text
hermes_cli/goals.py
```

Added:

- `get_agent_role_topology_fixture("openclaw_video_v1")`
- `agent_role_topology_v1` fixture with roles:
  - Planner
  - Coder
  - Tester
  - Reviewer
  - Deploy
- `compile_workflow_plan_v1(..., role_topology=fixture)` support.

## Safety semantics

The fixture is dry-run/projection-only:

- every role node has `execute=false`;
- every role node requires evidence;
- Deploy has `permission_boundary=external_side_effect_human_gate`;
- Deploy depends on Reviewer and cannot execute automatically;
- no OpenClaw/Niuma code is run;
- no deployment, taskpool lifecycle mutation, or external side effect is opened by the compiler.

## Test proof

Test added:

```text
tests/hermes_cli/test_goals.py::TestGoalManager::test_agent_role_topology_fixture_compiles_to_dag_without_deploy_execution
```

Observed RED before implementation:

```text
ImportError: cannot import name 'get_agent_role_topology_fixture' from 'hermes_cli.goals'
```

Observed GREEN after implementation:

```text
1 passed
```

## Acceptance mapping

Task acceptance:

- Verify whether Niuma has public source → `not_verified`, explicitly recorded.
- Verify OpenClaw repo via GitHub API → verified.
- Extract role topology contract → roles include inputs, outputs, evidence_contract, dependencies, permission_boundary.
- Map to GoalSpec → workflow_plan_v1 → compiler now supports `role_topology`.
- Add no-execute Deploy fixture → Deploy has `execute=false` and `human_gate_required=true`.
