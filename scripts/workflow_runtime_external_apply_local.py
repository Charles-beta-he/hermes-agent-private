#!/usr/bin/env python3
"""Apply the workflow external-apply contract to the local taskpool outbox simulator.

This is the first non-dry-run apply executor, but it is intentionally scoped to a
local simulator artifact. It does not call external APIs, send notifications, or
enable external side effects.
"""
from __future__ import annotations
import argparse, ast, hashlib, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[1]
HERMES_HOME=Path.home()/'.hermes'
ORCHESTRATOR_PATH=HERMES_HOME/'scripts'/'brain_task_orchestrator.py'
DEFAULT_TASK_ID='2026-06-13-workflow-kernel-consolidated-candidatecontract-acceptance-governance'
DEFAULT_CONTRACT_ID='xcontract_038531fcd31e024e'
DEFAULT_TARGET_ADAPTER='taskpool_outbox_simulator'
DEFAULT_OUTPUT_DIR=REPO_ROOT/'docs'/'private'/'workflow-kernel-runtime-external-apply-outbox'
ACTOR='workflow_runtime_external_apply_local'

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _digest(o:Any)->str: return hashlib.sha256(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def _write_json(path:Path,payload:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode()
    path.write_bytes(data)
    return {'path':str(path),'sha256':_sha(data),'bytes':len(data)}
def _load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod
def _is_false(v:Any)->bool:
    if isinstance(v,bool): return v is False
    if v is None: return True
    return str(v).strip().lower() in {'false','0','none',''}
def _object(v: Any) -> dict:
    if isinstance(v, dict): return v
    if isinstance(v, str) and v.strip():
        try:
            parsed=json.loads(v)
        except json.JSONDecodeError:
            parsed=ast.literal_eval(v)
        if isinstance(parsed, dict): return parsed
    return {}
def load_orchestrator(path=ORCHESTRATOR_PATH): return _load(path,'_workflow_external_apply_local_orchestrator')
def find_task(o, task_id):
    for t in o.iter_tasks():
        if t.id==task_id: return t
    raise SystemExit(f'task not found: {task_id}')

def execute_local_apply(o, *, task_id=DEFAULT_TASK_ID, contract_id=DEFAULT_CONTRACT_ID, target_adapter=DEFAULT_TARGET_ADAPTER, output_dir=DEFAULT_OUTPUT_DIR, write=False):
    if target_adapter != DEFAULT_TARGET_ADAPTER:
        raise SystemExit('local apply currently supports only taskpool_outbox_simulator')
    task=find_task(o,task_id); fm=task.fm
    if str(fm.get('workflow_external_apply_contract_id') or '') != contract_id:
        raise SystemExit('task contract id mismatch')
    if str(fm.get('workflow_external_apply_contract_status') or '') != 'real_apply_contracts_defined_no_target_selected':
        raise SystemExit('task contract status is not ready for local apply')
    if not _is_false(fm.get('external_side_effects_enabled')):
        raise SystemExit('refusing local apply because external_side_effects_enabled is true-ish')
    contract_record=_object(fm.get('workflow_external_apply_contract_record'))
    adapter=(((contract_record.get('target_adapters') or {}).get(target_adapter)) or {})
    if adapter.get('kind') != 'local_simulator':
        raise SystemExit('target adapter is not the local simulator')
    apply_id='xlocal_'+_digest({'contract_id':contract_id,'target_adapter':target_adapter,'actor':ACTOR})[:16]
    outbox_item={
        'outbox_item_id':apply_id,
        'target_adapter':target_adapter,
        'operation':'record_local_external_apply_intent',
        'source_task_id':task_id,
        'source_contract_id':contract_id,
        'external_side_effects_enabled':False,
        'delivery_status':'local_outbox_recorded_not_dispatched',
        'created_at':_now(),
        'payload':{
            'intent':'If a future human authorizes an actual external adapter, use this local outbox record as the digest-bound input, not as authority to dispatch.',
            'allowed_next_targets':['lark_task','github_issue','telegram_notification'],
            'safe_default':'do_not_dispatch'
        }
    }
    outbox_art=_write_json(output_dir/f'{apply_id}.taskpool_outbox_simulator.item.json',outbox_item)
    receipt={
        'receipt_id':apply_id,
        'target_adapter':target_adapter,
        'operation':'record_local_external_apply_intent',
        'apply_mode':'real_apply',
        'external_receipt':{'kind':'local_outbox_simulator','outbox_item':outbox_art,'dispatched':False},
        'post_apply_readback':{'source_task_id':task_id,'expected_contract_id':contract_id,'verified':False},
        'verification_status':'PENDING',
        'rollback_evidence_required':True,
    }
    receipt_art=_write_json(output_dir/f'{apply_id}.runtime_external_apply_receipt.json',receipt)
    rollback={
        'rollback_id':'xrollback_'+_digest({'receipt_id':apply_id,'mode':'not_needed_no_external_mutation'})[:16],
        'target_adapter':target_adapter,
        'source_receipt_id':apply_id,
        'rollback_mode':'not_needed_no_external_mutation',
        'rollback_receipt':{'reason':'Local simulator item was recorded as immutable evidence and was not dispatched externally.'},
        'post_rollback_readback':{'external_mutation_found':False,'local_outbox_item_retained_for_audit':True},
        'verification_status':'PASS'
    }
    rollback_art=_write_json(output_dir/f'{apply_id}.rollback_evidence.json',rollback)
    packet={
        'local_apply_id':apply_id,
        'contract_id':contract_id,
        'target_adapter':target_adapter,
        'apply_status':'local_outbox_simulator_apply_recorded',
        'apply_mode':'local_real_apply_no_external_dispatch',
        'external_side_effects_enabled':False,
        'outbox_item':outbox_art,
        'receipt':receipt_art,
        'rollback_evidence':rollback_art,
        'verification':{'status':'PENDING','checks':['task_contract_read','target_adapter_is_local_simulator','outbox_item_written','receipt_written','rollback_evidence_written','external_side_effects_disabled']},
        'readback_verified':False,
        'taskpool_write_enabled':bool(write),
        'boundary':'local_outbox_recorded_external_dispatch_still_blocked'
    }
    if write:
        fm['workflow_external_local_apply_id']=apply_id
        fm['workflow_external_local_apply_status']='local_outbox_simulator_apply_recorded'
        fm['workflow_external_local_apply_record']=packet
        fm['workflow_external_apply_selected_target']=target_adapter
        fm['external_side_effects_enabled']=False
        ev=list(fm.get('evidence') or [])
        for marker in [f'artifact:local_external_apply_outbox_item:{outbox_art["sha256"]}',f'artifact:runtime_external_apply_receipt:{receipt_art["sha256"]}',f'artifact:runtime_external_apply_rollback_evidence:{rollback_art["sha256"]}','proof:local_outbox_recorded_no_external_dispatch','classification:workflow_kernel_local_apply_recorded']:
            if marker not in ev: ev.append(marker)
        fm['evidence']=ev
        o.save_task(task)
        rb=find_task(o,task_id)
        packet['readback_verified']=str(rb.fm.get('workflow_external_local_apply_id') or '')==apply_id and _is_false(rb.fm.get('external_side_effects_enabled'))
        receipt['post_apply_readback']['verified']=packet['readback_verified']
        receipt['verification_status']='PASS' if packet['readback_verified'] else 'FAIL'
        receipt_art=_write_json(output_dir/f'{apply_id}.runtime_external_apply_receipt.json',receipt)
        packet['receipt']=receipt_art
        packet['verification']['status']='PASS' if packet['readback_verified'] else 'FAIL'
        packet['derived_refresh']=o.refresh_derived_task_views('workflow_runtime_external_apply_local')
    packet_art=_write_json(output_dir/f'{apply_id}.runtime_external_apply_local_packet.json',packet)
    packet['packet_artifact']=packet_art
    return packet

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--task-id',default=DEFAULT_TASK_ID); ap.add_argument('--contract-id',default=DEFAULT_CONTRACT_ID); ap.add_argument('--target-adapter',default=DEFAULT_TARGET_ADAPTER)
    ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT_DIR); ap.add_argument('--orchestrator',type=Path,default=ORCHESTRATOR_PATH); ap.add_argument('--write',action='store_true'); ap.add_argument('--json',action='store_true',dest='as_json')
    a=ap.parse_args(argv)
    res=execute_local_apply(load_orchestrator(a.orchestrator),task_id=a.task_id,contract_id=a.contract_id,target_adapter=a.target_adapter,output_dir=a.output_dir,write=a.write)
    if a.as_json or not a.write: print(json.dumps(res,indent=2,ensure_ascii=False,sort_keys=True))
    else: print(res['packet_artifact']['path'])
    return 0
if __name__=='__main__': raise SystemExit(main())
