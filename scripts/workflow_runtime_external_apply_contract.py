#!/usr/bin/env python3
"""Define all real external-apply requirements without enabling external apply.

This phase turns the open "which external adapter?" boundary into an explicit
capability registry, receipt schema, rollback schema, and human decision packet.
It updates taskpool evidence only; it never calls third-party APIs.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[1]
HERMES_HOME=Path.home()/'.hermes'
ORCHESTRATOR_PATH=HERMES_HOME/'scripts'/'brain_task_orchestrator.py'
DEFAULT_TASK_ID='2026-06-13-workflow-kernel-consolidated-candidatecontract-acceptance-governance'
DEFAULT_APPLY_ID='xapply_a8db35ea417dec53'
DEFAULT_REGISTRY=REPO_ROOT/'config'/'target_adapter_capability_registry.json'
DEFAULT_RECEIPT_SCHEMA=REPO_ROOT/'schemas'/'runtime_external_apply_receipt.schema.json'
DEFAULT_ROLLBACK_SCHEMA=REPO_ROOT/'schemas'/'runtime_external_apply_rollback_evidence.schema.json'
DEFAULT_OUT=REPO_ROOT/'docs'/'private'/'workflow-kernel-runtime-external-apply-contracts'
ACTOR='workflow_runtime_external_apply_contract'

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _read_json(path:Path): return json.loads(path.read_text(encoding='utf-8'))
def _is_false(value: Any) -> bool:
    if isinstance(value, bool): return value is False
    if value is None: return True
    return str(value).strip().lower() in {'false', '0', 'none', ''}
def _digest(o:Any)->str: return _sha(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
def _write_json(path:Path,payload:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode()
    path.write_bytes(data)
    return {'path':str(path),'sha256':_sha(data),'bytes':len(data)}
def _load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod
def find_task(o, task_id):
    for t in o.iter_tasks():
        if t.id==task_id: return t
    raise SystemExit(f'task not found: {task_id}')
def load_orchestrator(path=ORCHESTRATOR_PATH): return _load(path,'_workflow_external_apply_contract_orchestrator')

def build_contract(o, *, task_id=DEFAULT_TASK_ID, apply_id=DEFAULT_APPLY_ID, registry_path=DEFAULT_REGISTRY, receipt_schema=DEFAULT_RECEIPT_SCHEMA, rollback_schema=DEFAULT_ROLLBACK_SCHEMA, output_dir=DEFAULT_OUT, write=False):
    registry=_read_json(registry_path)
    receipt_bytes=receipt_schema.read_bytes(); rollback_bytes=rollback_schema.read_bytes()
    task=find_task(o,task_id); fm=task.fm
    if str(fm.get('workflow_external_apply_id') or '') != apply_id: raise SystemExit('task external apply id mismatch')
    if str(fm.get('workflow_external_apply_status') or '') != 'external_apply_scaffold_dry_run_ready': raise SystemExit('task not ready for external apply contract definition')
    if not _is_false(fm.get('external_side_effects_enabled')):
        raise SystemExit('refusing contract because external_side_effects_enabled is true-ish')
    adapters=registry.get('adapters') or {}
    if not adapters: raise SystemExit('adapter capability registry has no adapters')
    options=[f'{name}: {cfg.get("status")} / {cfg.get("required_authority")}' for name,cfg in sorted(adapters.items())]
    contract_id='xcontract_'+_digest({'apply_id':apply_id,'registry_version':registry.get('version'), 'actor':ACTOR})[:16]
    packet={
        'contract_id':contract_id,
        'registry_version':registry.get('version'),
        'target_adapters':adapters,
        'selection_status':'target_adapter_options_fully_enumerated_pending_selection',
        'required_human_decision':{
            'decision':'Select one target adapter and authorize exact operation scope before any real external apply executor can run.',
            'options':options,
            'safe_default':'Keep all external side effects disabled; continue with local dry-run and contract-only work.'
        },
        'real_apply_enabled':False,
        'external_side_effects_enabled':False,
        'contract_status':'real_apply_contracts_defined_no_target_selected',
        'receipt_schema':{'path':str(receipt_schema),'sha256':_sha(receipt_bytes),'title':_read_json(receipt_schema).get('title')},
        'rollback_schema':{'path':str(rollback_schema),'sha256':_sha(rollback_bytes),'title':_read_json(rollback_schema).get('title')},
        'verification':{'status':'PASS','checks':['adapter_registry_loaded','all_target_options_enumerated','receipt_schema_digest_recorded','rollback_schema_digest_recorded','real_apply_disabled','external_side_effects_disabled']},
        'taskpool_write_enabled':bool(write),
        'readback_verified':False,
        'boundary':'all_external_apply_requirements_defined_real_apply_still_blocked',
        'created_at':_now()
    }
    art=_write_json(output_dir/f'{contract_id}.runtime_external_apply_contract_packet.json',packet)
    packet['packet_artifact']=art
    if write:
        fm['workflow_external_apply_contract_id']=contract_id
        fm['workflow_external_apply_contract_status']='real_apply_contracts_defined_no_target_selected'
        fm['workflow_external_apply_contract_record']=packet
        fm['external_side_effects_enabled']=False
        ev=list(fm.get('evidence') or [])
        for marker in [f'artifact:runtime_external_apply_contract_packet:{contract_id}','proof:target_adapter_options_fully_enumerated','proof:real_apply_still_blocked_until_target_selection','classification:workflow_kernel_external_apply_requirements_defined']:
            if marker not in ev: ev.append(marker)
        fm['evidence']=ev
        o.save_task(task)
        rb=find_task(o,task_id)
        packet['readback_verified']=str(rb.fm.get('workflow_external_apply_contract_id') or '')==contract_id and _is_false(rb.fm.get('external_side_effects_enabled'))
        packet['derived_refresh']=o.refresh_derived_task_views('workflow_runtime_external_apply_contract')
        art=_write_json(output_dir/f'{contract_id}.runtime_external_apply_contract_packet.json',packet)
        packet['packet_artifact']=art
    return packet

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--task-id',default=DEFAULT_TASK_ID); ap.add_argument('--external-apply-id',default=DEFAULT_APPLY_ID)
    ap.add_argument('--registry',type=Path,default=DEFAULT_REGISTRY); ap.add_argument('--receipt-schema',type=Path,default=DEFAULT_RECEIPT_SCHEMA); ap.add_argument('--rollback-schema',type=Path,default=DEFAULT_ROLLBACK_SCHEMA)
    ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUT); ap.add_argument('--orchestrator',type=Path,default=ORCHESTRATOR_PATH); ap.add_argument('--write',action='store_true'); ap.add_argument('--json',action='store_true',dest='as_json')
    a=ap.parse_args(argv)
    res=build_contract(load_orchestrator(a.orchestrator),task_id=a.task_id,apply_id=a.external_apply_id,registry_path=a.registry,receipt_schema=a.receipt_schema,rollback_schema=a.rollback_schema,output_dir=a.output_dir,write=a.write)
    if a.as_json or not a.write: print(json.dumps(res,indent=2,ensure_ascii=False,sort_keys=True))
    else: print(res['packet_artifact']['path'])
    return 0
if __name__=='__main__': raise SystemExit(main())
