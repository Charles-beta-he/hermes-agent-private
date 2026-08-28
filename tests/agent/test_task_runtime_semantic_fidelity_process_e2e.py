from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[2]


def _run(code: str, *, db_path: Path) -> dict:
    env = os.environ.copy()
    env["HERMES_TASK_RUNTIME_DB"] = str(db_path)
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=True,
    )
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _strict_fixture() -> dict:
    def source(source_id, source_kind, content, authority_rank):
        digest = "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()
        return {
            "source_id": source_id,
            "kind": "user_message",
            "source_kind": source_kind,
            "content": content,
            "immutable_locator": f"inline:{source_id}@{digest}",
            "content_digest": digest,
            "authority_rank": authority_rank,
            "derived_from": [],
        }

    obligations = {
        "pages": ["list", "detail"],
        "states": [
            "list/loading",
            "list/success",
            "list/empty",
            "detail/success",
            "detail/error",
        ],
        "interactions": ["list-to-detail", "detail-back-to-list"],
        "viewports": ["1440x900"],
        "negative_cases": [
            "no-generic-list-empty",
            "no-generic-detail-error",
            "preserve-list-state-on-back",
        ],
        "fidelity_dimensions": ["visual", "interaction", "responsive"],
    }
    obligation_refs = [
        f"{field}:{value}" for field, values in obligations.items() for value in values
    ]
    sources = [
        source("REQ", "original_requirement", "strictly reproduce all UI states", 10),
        source("PROTO", "approved_prototype", "frozen list/detail oracle", 20),
    ]
    discovery = {
        lane: {
            "status": "completed" if lane == "project_evidence" else "not_applicable",
            "source_refs": ["REQ"],
            "decision": "fixture evidence inspected" if lane == "project_evidence" else "not required",
        }
        for lane in ("project_evidence", "prototype", "competitor", "open_source")
    }
    required = {
        "id": "R1",
        "text": "reproduce every frozen UI obligation",
        "source_refs": ["REQ", "PROTO"],
        "source_anchors": [
            {
                "source_ref": "PROTO",
                "locator": sources[1]["immutable_locator"] + "#list-detail",
            }
        ],
        "scope_quantifier": "all",
        "obligation_refs": obligation_refs,
        "oracle_refs": ["PROTO"],
        "fidelity_dimensions": ["visual", "interaction", "responsive"],
    }
    spec = {
        "spec_id": "strict-process-spec",
        "governance": {
            "task_class": "complex",
            "target_profile": "strict_ui",
            "source_authority": ["REQ", "PROTO"],
            "oracle_refs": ["PROTO"],
            "source_coverage": {"REQ": ["R1", "N1"], "PROTO": ["R1", "N1"]},
            "source_obligations": obligations,
            "discovery": discovery,
            "blocking_unknowns": [],
        },
        "required_clauses": [required],
        "negative_requirements": [
            {
                "id": "N1",
                "text": "do not substitute generic empty or error states",
                "source_refs": ["REQ", "PROTO"],
                "source_anchors": required["source_anchors"],
                "scope_quantifier": "all",
                "obligation_refs": ["negative_cases:no-generic-detail-error"],
                "oracle_refs": ["PROTO"],
                "fidelity_dimensions": ["visual"],
            }
        ],
        "closure_criteria": ["R1", "N1"],
    }
    definition = {
        "slice_id": "strict-slice",
        "role": "implementer",
        "objective": "implement frozen strict UI fixture",
        "dependencies": [],
        "required_clauses": ["R1"],
        "negative_requirements": ["N1"],
        "source_refs": ["REQ", "PROTO"],
        "obligation_refs": obligation_refs,
        "source_anchors": required["source_anchors"],
        "oracle_refs": ["PROTO"],
        "fidelity_dimensions": ["visual", "interaction", "responsive"],
        "architecture_invariants": ["semantic fidelity"],
        "owned_paths": ["src/strict-ui.py"],
        "forbidden_changes": [],
        "verification_commands": ["pytest -q tests/strict-ui.py"],
        "authority": {"side_effect_ceiling": "local_reversible"},
    }
    return {"sources": sources, "spec": spec, "definition": definition}


def test_strict_ui_semantic_fidelity_reaches_closure_and_invalidates_across_processes(
    tmp_path,
):
    db_path = tmp_path / "strict-process.db"
    fixture = json.dumps(_strict_fixture())
    closed = _run(
        f"""
import json
from agent.task_runtime import TaskRuntimeStore
fixture = json.loads({fixture!r})
store = TaskRuntimeStore(TaskRuntimeStore.default_path())
task = store.create_task(title='strict process e2e', original_sources=fixture['sources'])
review = store.record_spec_review(task['task_id'], fixture['spec'], reviewer_session_id='reviewer', verdict='approved', findings=[], expected_state_revision=task['state_revision'])
task = store.freeze_spec(task['task_id'], fixture['spec'], compiler_session_id='compiler', expected_state_revision=review['state_revision'])
task = store.set_plan(task['task_id'], [fixture['definition']], expected_state_revision=task['state_revision'])
packet = store.claim_next_slice(task['task_id'], worker_session_id='worker', lease_seconds=60)
binding = {{'kind':'ui-fidelity','oracle_refs':packet['oracle_refs'],'candidate_digest':'sha256:process-candidate','source_digest':packet['source_digest'],'spec_digest':packet['spec_digest']}}
evidence = [dict(binding, ref='uimax:process-r1-pass', claim_id='R1', obligation_refs=packet['obligation_refs'], fidelity_dimensions=packet['fidelity_dimensions'], source_anchors=packet['claim_contracts']['R1']['source_anchors']), dict(binding, ref='uimax:process-n1-pass', claim_id='N1', obligation_refs=['negative_cases:no-generic-detail-error'], fidelity_dimensions=['visual'], source_anchors=packet['claim_contracts']['N1']['source_anchors'])]
result = {{'slice_id':packet['slice_id'],'input_digest':packet['packet_digest'],'claims':['R1','N1'],'changed_artifacts':['src/strict-ui.py'],'evidence_refs':evidence,'verification_results':[{{'command':'pytest -q tests/strict-ui.py','exit_code':0}}],'risks':[],'unknowns':[],'state_delta_proposal':{{'slice_status':'completed'}},'candidate_digest':'sha256:process-candidate'}}
submitted = store.submit_result(task['task_id'], worker_session_id='worker', packet_id=packet['packet_id'], result=result)
store.integrate_result(task['task_id'], submitted['result_id'], integrator_session_id='integrator')
closure_packet = store.build_closure_packet(task['task_id'], verifier_session_id='verifier')
closed = store.close_task(task['task_id'], candidate_digest='sha256:process-candidate', verifier_session_id='verifier', claims={{'R1':'pass','N1':'pass'}}, closure_packet=closure_packet, evidence_refs=evidence, verification_results=[{{'command':'pytest -q tests/strict-ui.py','exit_code':0,'candidate_digest':'sha256:process-candidate'}}])
print(json.dumps({{'task_id':task['task_id'],'status':closed['status'],'closure':closed['closure']}}, sort_keys=True))
""",
        db_path=db_path,
    )
    assert closed["status"] == "closed"
    assert closed["closure"]["candidate_digest"] == "sha256:process-candidate"
    assert closed["closure"]["source_digest"].startswith("sha256:")
    assert closed["closure"]["spec_digest"].startswith("sha256:")
    assert closed["closure"]["obligation_digest"].startswith("sha256:")
    assert closed["closure"]["verifier_session_id"] == "verifier"

    invalidated = _run(
        f"""
import hashlib, json
from agent.task_runtime import TaskRuntimeStore
store = TaskRuntimeStore(TaskRuntimeStore.default_path())
content = 'also support 390x844 viewport'
digest = 'sha256:' + hashlib.sha256(content.encode('utf-8')).hexdigest()
task = store.append_source_event({closed['task_id']!r}, {{'event_id':'VIEWPORT390','kind':'user_message','source_kind':'original_requirement','content':content,'immutable_locator':'inline:VIEWPORT390@'+digest,'content_digest':digest,'authority_rank':15,'derived_from':[]}})
try:
    store.claim_next_slice(task['task_id'], worker_session_id='stale-worker', lease_seconds=60)
    claim_error = ''
except ValueError as exc:
    claim_error = str(exc)
print(json.dumps({{'status':task['status'],'implementation_graph':task['implementation_graph'],'candidate_digest':task['candidate_digest'],'evidence_graph':task['evidence_graph'],'closure':task['closure'],'claim_error':claim_error,'event':task['state_events'][-1]['payload']}}, sort_keys=True))
""",
        db_path=db_path,
    )
    assert invalidated["status"] == "draft"
    assert invalidated["implementation_graph"] == []
    assert invalidated["candidate_digest"] == ""
    assert invalidated["evidence_graph"] == []
    assert invalidated["closure"] == {}
    assert invalidated["claim_error"] == "task_not_claimable"
    assert invalidated["event"]["spec_invalidated"] is True
    assert invalidated["event"]["closure_invalidated"] is True
