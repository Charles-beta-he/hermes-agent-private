#!/usr/bin/env python3
"""Create a fail-closed human gate packet for external side effects.

This script consumes the local adapter outbox simulation and writes a structured
human-intervention packet. It may annotate the selected task with gate status,
but it never applies an external adapter, sends notifications, or mutates any
third-party system.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[1]
HERMES_HOME=Path.home()/'.hermes'
ORCHESTRATOR_PATH=HERMES_HOME/'scripts'/'brain_task_orchestrator.py'
DEFAULT_OUTPUT_DIR=REPO_ROOT/'docs'/'private'/'workflow-kernel-runtime-external-side-effect-gates'
DEFAULT_ADAPTER_WRITE_ID='adw_02a3cf0530913fb8'
DEFAULT_MODEL_CALL_ID='mcall_e01f7305ba2efb07'
DEFAULT_WORKFLOW_ID='decision_brief_pack'
DEFAULT_RUN_ID='run_preview_78eee6afcec1f12a'
DEFAULT_GROUP_ID='contract_acceptance_governance'
ACTOR='workflow_runtime_external_side_effect_gate'

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def _digest(o:Any)->str:
    return 'sha256:'+hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _fm_bool(v:Any)->bool: return v if isinstance(v,bool) else str(v or '').strip().lower() in {'1','true','yes','y','on'}
def _load(path:Path,name:str):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod
def load_orchestrator(path:Path=ORCHESTRATOR_PATH): return _load(path,'_workflow_external_gate_orchestrator')
def _status(t): return str(getattr(t,'status','') or t.fm.get('status') or '')
def _readback(o,tid):
    return next((t for t in o.iter_tasks() if t.id==tid), None)

def find_task(o, adapter_write_id, model_call_id, group_id):
    ms=[]
    for t in o.iter_tasks():
        fm=t.fm
        if str(fm.get('workflow_adapter_write_id') or '')==adapter_write_id and str(fm.get('workflow_model_call_id') or '')==model_call_id and str(fm.get('workflow_consolidation_group_id') or '')==group_id:
            ms.append(t)
    if not ms: raise SystemExit('no adapter-write task found for external side-effect gate')
    if len(ms)>1: raise SystemExit('multiple adapter-write tasks found; refusing ambiguous gate')
    return ms[0]

def build_gate_id(adapter_write_id, model_call_id, task_id):
    return 'xgate_'+_digest({'adapter_write_id':adapter_write_id,'model_call_id':model_call_id,'task_id':task_id,'actor':ACTOR}).split(':',1)[1][:16]

def build_packet(task, *, adapter_write_id, model_call_id, workflow_id, run_id, group_id):
    gate_id=build_gate_id(adapter_write_id, model_call_id, task.id)
    return {
        'external_gate_id': gate_id,
        'adapter_write_id': adapter_write_id,
        'model_call_id': model_call_id,
        'workflow_id': workflow_id,
        'run_id': run_id,
        'generated_at': _now(),
        'selected_group_id': group_id,
        'selected_task_id': task.id,
        'gate_status': 'blocked_pending_external_target_authorization',
        'decision_needed': 'Choose and explicitly authorize one external adapter target before any third-party mutation or notification is allowed.',
        'confirmed_facts': [
            'local workflow runtime chain reached done',
            'read-only model call completed and was verified',
            'adapter write-through exists only as local taskpool outbox simulation',
            'external_side_effects_enabled=false'
        ],
        'unconfirmed_facts': [
            'which external adapter target should receive the write',
            'whether credentials and permissions for that target are in scope',
            'whether the external mutation should be sent now or only staged for review'
        ],
        'options': [
            {'id':'A','label':'keep local-only','effect':'do not apply external mutation; continue building review/apply tooling'},
            {'id':'B','label':'authorize named adapter dry-run','effect':'user names one adapter; executor performs target-specific dry-run/readiness only'},
            {'id':'C','label':'authorize named adapter apply','effect':'user names one adapter and approves mutation with rollback and post-apply verification'}
        ],
        'recommended_default': 'A: keep local-only until a concrete external target adapter is named.',
        'blast_radius': 'No current blast radius: this packet does not mutate external systems. Future apply blast radius is limited to the single named adapter and selected task.',
        'rollback_plan': 'Remove workflow_external_side_effect_gate_id/status/record and matching evidence markers; no external rollback is needed because no external mutation was performed.',
        'post_action_verification': ['read back target adapter object', 'compare digest/fields with authorized intent', 'record apply receipt/status', 'confirm external_side_effects_status'],
        'taskpool_write_enabled': True,
        'external_side_effects_enabled': False,
        'readback_verified': True,
        'derived_refresh': None,
        'boundary': 'external_side_effect_gate_packet_written_no_external_mutation'
    }

def write_json(path:Path,payload:dict)->dict:
    path.parent.mkdir(parents=True,exist_ok=True); data=(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode(); path.write_bytes(data)
    return {'path':str(path),'sha256':_sha(data),'bytes':len(data)}

def execute_gate(o, *, adapter_write_id=DEFAULT_ADAPTER_WRITE_ID, model_call_id=DEFAULT_MODEL_CALL_ID, workflow_id=DEFAULT_WORKFLOW_ID, run_id=DEFAULT_RUN_ID, group_id=DEFAULT_GROUP_ID, output_dir=DEFAULT_OUTPUT_DIR, write=False):
    task=find_task(o,adapter_write_id,model_call_id,group_id)
    if _status(task)!='done': raise SystemExit(f'external gate expects done task, got {_status(task)}')
    if str(task.fm.get('workflow_adapter_write_status') or '')!='adapter_write_local_outbox_simulated': raise SystemExit('external gate expects local outbox simulation')
    if _fm_bool(task.fm.get('external_side_effects_enabled')): raise SystemExit('external gate refuses task with external side effects already enabled')
    packet=build_packet(task,adapter_write_id=adapter_write_id,model_call_id=model_call_id,workflow_id=workflow_id,run_id=run_id,group_id=group_id)
    art=write_json(output_dir/f"{workflow_id}.{run_id}.{packet['external_gate_id']}.external_side_effect_gate_packet.json", packet)
    packet['packet_artifact']=art
    if write:
        task.fm['workflow_external_side_effect_gate_id']=packet['external_gate_id']
        task.fm['workflow_external_side_effect_gate_status']=packet['gate_status']
        task.fm['workflow_external_side_effect_gate_record']=packet
        task.fm['external_side_effects_enabled']=False
        ev=list(task.fm.get('evidence') or [])
        for marker in [f"artifact:runtime_external_side_effect_gate_packet:{packet['external_gate_id']}", 'proof:external_side_effect_gate_no_external_mutation', 'classification:workflow_kernel_external_side_effect_gate_blocked_pending_target']:
            if marker not in ev: ev.append(marker)
        task.fm['evidence']=ev; o.save_task(task)
        rb=_readback(o,task.id)
        if not rb or str(rb.fm.get('workflow_external_side_effect_gate_id') or '')!=packet['external_gate_id']: raise SystemExit('external gate readback mismatch')
        if _fm_bool(rb.fm.get('external_side_effects_enabled')): raise SystemExit('external flag unexpectedly enabled')
        packet['readback_verified']=True
        packet['derived_refresh']=o.refresh_derived_task_views('workflow_runtime_external_side_effect_gate')
    else:
        packet['readback_verified']=False
    return packet

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--adapter-write-id',default=DEFAULT_ADAPTER_WRITE_ID); ap.add_argument('--model-call-id',default=DEFAULT_MODEL_CALL_ID)
    ap.add_argument('--workflow-id',default=DEFAULT_WORKFLOW_ID); ap.add_argument('--run-id',default=DEFAULT_RUN_ID); ap.add_argument('--group-id',default=DEFAULT_GROUP_ID)
    ap.add_argument('--orchestrator',type=Path,default=ORCHESTRATOR_PATH); ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT_DIR)
    ap.add_argument('--write',action='store_true'); ap.add_argument('--json',action='store_true',dest='as_json')
    a=ap.parse_args(argv); res=execute_gate(load_orchestrator(a.orchestrator),adapter_write_id=a.adapter_write_id,model_call_id=a.model_call_id,workflow_id=a.workflow_id,run_id=a.run_id,group_id=a.group_id,output_dir=a.output_dir,write=a.write)
    if a.as_json or not a.write: print(json.dumps(res,indent=2,ensure_ascii=False,sort_keys=True))
    else: print(res['packet_artifact']['path'])
    return 0
if __name__=='__main__': raise SystemExit(main())
