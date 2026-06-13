#!/usr/bin/env python3
"""Target-specific external apply executor scaffold (dry-run only).

Requires --target-adapter and --authorization-packet. It validates the external
side-effect gate packet, writes a dry-run receipt, annotates the selected task,
and refuses real external mutation.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[1]
HERMES_HOME=Path.home()/'.hermes'
ORCHESTRATOR_PATH=HERMES_HOME/'scripts'/'brain_task_orchestrator.py'
DEFAULT_OUTPUT_DIR=REPO_ROOT/'docs'/'private'/'workflow-kernel-runtime-external-apply-scaffold'
DEFAULT_GATE_ID='xgate_4b4a9625d638fafa'
DEFAULT_ADAPTER_WRITE_ID='adw_02a3cf0530913fb8'
DEFAULT_TASK_ID='2026-06-13-workflow-kernel-consolidated-candidatecontract-acceptance-governance'
ALLOWED_DRY_RUN_TARGETS={'taskpool_outbox_simulator','lark_task_dry_run','github_issue_dry_run','telegram_notification_dry_run'}
ACTOR='workflow_runtime_external_apply_scaffold'

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _digest(o:Any)->str: return 'sha256:'+_sha(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
def _fm_bool(v): return v if isinstance(v,bool) else str(v or '').strip().lower() in {'1','true','yes','y','on'}
def _load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod
def load_orchestrator(path=ORCHESTRATOR_PATH): return _load(path,'_workflow_external_apply_orchestrator')
def _read_json(path:Path): return json.loads(path.read_text(encoding='utf-8'))
def _write_json(path:Path,payload:dict):
    path.parent.mkdir(parents=True,exist_ok=True); data=(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode(); path.write_bytes(data)
    return {'path':str(path),'sha256':_sha(data),'bytes':len(data)}
def find_task(o, task_id):
    for t in o.iter_tasks():
        if t.id==task_id: return t
    raise SystemExit(f'task not found: {task_id}')
def build_apply_id(gate_id, adapter_write_id, target): return 'xapply_'+_digest({'gate':gate_id,'adapter_write':adapter_write_id,'target':target,'actor':ACTOR}).split(':',1)[1][:16]

def execute_apply_scaffold(o, *, target_adapter:str, authorization_packet:Path, task_id=DEFAULT_TASK_ID, external_gate_id=DEFAULT_GATE_ID, adapter_write_id=DEFAULT_ADAPTER_WRITE_ID, output_dir=DEFAULT_OUTPUT_DIR, write=False):
    if not target_adapter: raise SystemExit('--target-adapter is required')
    if target_adapter not in ALLOWED_DRY_RUN_TARGETS: raise SystemExit(f'target adapter not allowed for dry-run scaffold: {target_adapter}')
    if not authorization_packet or not authorization_packet.exists(): raise SystemExit('--authorization-packet path is required and must exist')
    auth_bytes=authorization_packet.read_bytes(); auth_sha=_sha(auth_bytes); auth=_read_json(authorization_packet)
    if auth.get('external_gate_id')!=external_gate_id: raise SystemExit('authorization packet gate id mismatch')
    if auth.get('gate_status')!='blocked_pending_external_target_authorization': raise SystemExit('authorization packet is not a pending external gate packet')
    if auth.get('external_side_effects_enabled') is not False: raise SystemExit('authorization packet unexpectedly enables external side effects')
    t=find_task(o,task_id); fm=t.fm
    if str(fm.get('workflow_external_side_effect_gate_id') or '')!=external_gate_id: raise SystemExit('task gate id mismatch')
    if str(fm.get('workflow_adapter_write_id') or '')!=adapter_write_id: raise SystemExit('task adapter write id mismatch')
    if _fm_bool(fm.get('external_side_effects_enabled')): raise SystemExit('refusing scaffold because task external_side_effects_enabled=true')
    apply_id=build_apply_id(external_gate_id,adapter_write_id,target_adapter)
    receipt={'external_apply_id':apply_id,'target_adapter':target_adapter,'would_apply':False,'apply_mode':'dry_run_only','authorized_gate_id':external_gate_id,'adapter_write_id':adapter_write_id,'created_at':_now(),'reason':'scaffold validates target and authorization only; no external mutation is performed'}
    receipt_suffix = 'dry_run_receipt' if write else 'dry_run_receipt_preview'
    receipt_art=_write_json(output_dir/f'{apply_id}.{target_adapter}.{receipt_suffix}.json',receipt)
    packet={'external_apply_id':apply_id,'external_gate_id':external_gate_id,'adapter_write_id':adapter_write_id,'target_adapter':target_adapter,'authorization_packet':{'path':str(authorization_packet),'sha256':auth_sha},'apply_mode':'dry_run_only','apply_status':'external_apply_scaffold_dry_run_ready','dry_run_receipt':receipt_art|{'target_adapter':target_adapter,'would_apply':False},'verification':{'status':'PASS','checks':['target_adapter_required','authorization_packet_read','authorization_gate_pending','task_readback_matches_gate','dry_run_receipt_written','external_side_effects_disabled']},'taskpool_write_enabled':bool(write),'external_side_effects_enabled':False,'readback_verified':False,'derived_refresh':None,'rollback_plan':'Remove workflow_external_apply_id/status/record and evidence markers; delete dry-run receipt if desired. No external rollback needed because no mutation occurred.','boundary':'external_apply_executor_scaffold_dry_run_only_no_external_mutation'}
    packet_filename = f'{apply_id}.runtime_external_apply_scaffold_packet.json' if write else f'{apply_id}.runtime_external_apply_scaffold_preview_packet.json'
    packet_art=_write_json(output_dir/packet_filename,packet)
    packet['packet_artifact']=packet_art
    if write:
        fm['workflow_external_apply_id']=apply_id; fm['workflow_external_apply_status']='external_apply_scaffold_dry_run_ready'; fm['workflow_external_apply_record']=packet; fm['external_side_effects_enabled']=False
        ev=list(fm.get('evidence') or [])
        for marker in [f'artifact:runtime_external_apply_scaffold_packet:{apply_id}',f'artifact:external_apply_dry_run_receipt:{receipt_art["sha256"]}','proof:external_apply_scaffold_dry_run_no_external_mutation','classification:workflow_kernel_external_apply_scaffold_ready']:
            if marker not in ev: ev.append(marker)
        fm['evidence']=ev; o.save_task(t)
        rb=find_task(o,task_id)
        if str(rb.fm.get('workflow_external_apply_id') or '')!=apply_id: raise SystemExit('apply scaffold readback mismatch')
        if _fm_bool(rb.fm.get('external_side_effects_enabled')): raise SystemExit('external side effects unexpectedly enabled')
        packet['readback_verified']=True; packet['derived_refresh']=o.refresh_derived_task_views('workflow_runtime_external_apply_scaffold')
        packet_art=_write_json(output_dir/f'{apply_id}.runtime_external_apply_scaffold_packet.json',packet); packet['packet_artifact']=packet_art
    return packet

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target-adapter',required=True); ap.add_argument('--authorization-packet',type=Path,required=True)
    ap.add_argument('--task-id',default=DEFAULT_TASK_ID); ap.add_argument('--external-gate-id',default=DEFAULT_GATE_ID); ap.add_argument('--adapter-write-id',default=DEFAULT_ADAPTER_WRITE_ID)
    ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT_DIR); ap.add_argument('--orchestrator',type=Path,default=ORCHESTRATOR_PATH); ap.add_argument('--write',action='store_true'); ap.add_argument('--json',action='store_true',dest='as_json')
    a=ap.parse_args(argv); res=execute_apply_scaffold(load_orchestrator(a.orchestrator),target_adapter=a.target_adapter,authorization_packet=a.authorization_packet,task_id=a.task_id,external_gate_id=a.external_gate_id,adapter_write_id=a.adapter_write_id,output_dir=a.output_dir,write=a.write)
    if a.as_json or not a.write: print(json.dumps(res,indent=2,ensure_ascii=False,sort_keys=True))
    else: print(res['packet_artifact']['path'])
    return 0
if __name__=='__main__': raise SystemExit(main())
