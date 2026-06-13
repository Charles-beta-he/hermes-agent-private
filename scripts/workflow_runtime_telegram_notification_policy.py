#!/usr/bin/env python3
"""Governed Telegram notification request compiler and receipt ledger.

This script does not send Telegram messages. It enforces target allowlist,
idempotency, rate-limit checks, dry-run previews, and records receipts produced
by the Hermes `send_message` tool. Real sending remains an explicit tool action
outside this script.
"""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, sys, time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT=Path(__file__).resolve().parents[1]
HERMES_HOME=Path.home()/'.hermes'
ORCHESTRATOR_PATH=HERMES_HOME/'scripts'/'brain_task_orchestrator.py'
DEFAULT_POLICY=REPO_ROOT/'config'/'telegram_notification_policy.json'
DEFAULT_OUTPUT_DIR=REPO_ROOT/'docs'/'private'/'workflow-kernel-telegram-notification-policy'
DEFAULT_LEDGER=DEFAULT_OUTPUT_DIR/'telegram_notification_ledger.json'
DEFAULT_TASK_ID='2026-06-13-workflow-kernel-consolidated-candidatecontract-acceptance-governance'
ACTOR='workflow_runtime_telegram_notification_policy'

def _now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def _sha(b:bytes)->str: return hashlib.sha256(b).hexdigest()
def _digest(o:Any)->str: return _sha(json.dumps(o,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
def _read_json(path:Path, default=None):
    if not path.exists(): return default
    return json.loads(path.read_text(encoding='utf-8'))
def _write_json(path:Path,payload:dict):
    path.parent.mkdir(parents=True,exist_ok=True)
    data=(json.dumps(payload,indent=2,ensure_ascii=False,sort_keys=True)+'\n').encode()
    path.write_bytes(data)
    return {'path':str(path),'sha256':_sha(data),'bytes':len(data)}
def _load(path,name):
    spec=importlib.util.spec_from_file_location(name,path); assert spec and spec.loader
    mod=importlib.util.module_from_spec(spec); sys.modules[spec.name]=mod; spec.loader.exec_module(mod); return mod
def load_orchestrator(path=ORCHESTRATOR_PATH): return _load(path,'_telegram_notification_policy_orchestrator')
def _find_task(o, task_id):
    for t in o.iter_tasks():
        if t.id==task_id: return t
    raise SystemExit(f'task not found: {task_id}')
def _allowed(policy, target, chat_id, purpose):
    for row in policy.get('allowlist') or []:
        if row.get('target')==target and str(row.get('chat_id'))==str(chat_id) and purpose in (row.get('allowed_purposes') or []): return row
    return None
def _load_ledger(path:Path):
    return _read_json(path, {'version':'2026-06-13.phase20.ledger.v1','entries':[]})
def _recent_count(ledger, *, target, chat_id, window_seconds, now_ts):
    count=0
    for e in ledger.get('entries') or []:
        if e.get('target')==target and str(e.get('chat_id'))==str(chat_id) and e.get('status') in {'sent','receipt_recorded'}:
            if now_ts - float(e.get('created_ts') or 0) <= window_seconds: count+=1
    return count

def compile_request(*, policy_path=DEFAULT_POLICY, ledger_path=DEFAULT_LEDGER, output_dir=DEFAULT_OUTPUT_DIR, target='telegram', chat_id='5524172309', purpose='system_test', message='', mode='dry_run', send_result_path:Path|None=None, write=False, orchestrator=None, task_id=DEFAULT_TASK_ID):
    policy=_read_json(policy_path)
    if not policy: raise SystemExit('policy missing')
    if mode not in {'dry_run','record_send_result'}: raise SystemExit('mode must be dry_run or record_send_result')
    if not message and mode=='dry_run': raise SystemExit('--message is required for dry_run')
    allow=_allowed(policy,target,chat_id,purpose)
    if not allow: raise SystemExit('target/purpose not allowed by telegram policy')
    ledger=_load_ledger(ledger_path)
    now_ts=time.time(); msg_digest='sha256:'+_sha(message.encode('utf-8')) if message else None
    key='tg_'+_digest({'target':target,'chat_id':str(chat_id),'purpose':purpose,'message_digest':msg_digest})[:16]
    existing=next((e for e in ledger.get('entries') or [] if e.get('idempotency_key')==key and e.get('status') in {'sent','receipt_recorded','dry_run_ready'}), None)
    rate_count=_recent_count(ledger,target=target,chat_id=str(chat_id),window_seconds=int(policy['rate_limit']['window_seconds']),now_ts=now_ts)
    packet={'request_id':key,'target':target,'chat_id':str(chat_id),'purpose':purpose,'message_digest':msg_digest,'mode':mode,'policy_version':policy.get('version'),'created_at':_now(),'allowed':True,'rate_limit':{'current_count':rate_count,'max':policy['rate_limit']['max_sends_per_window'],'window_seconds':policy['rate_limit']['window_seconds']},'idempotency':{'duplicate':bool(existing),'existing_receipt':existing},'unattended_send_enabled':bool(policy.get('unattended_send_enabled')),'send_instruction':None,'status':'PENDING','verification':{'status':'PENDING','checks':['policy_loaded','target_allowlisted','idempotency_checked','rate_limit_checked']}}
    if existing and mode=='dry_run':
        packet['status']='blocked_duplicate_existing_request'; packet['verification']['status']='PASS'
    elif rate_count >= int(policy['rate_limit']['max_sends_per_window']) and mode=='dry_run':
        packet['status']='blocked_rate_limit'; packet['verification']['status']='PASS'
    elif mode=='dry_run':
        packet['status']='dry_run_ready'; packet['send_instruction']={'tool':'send_message','target':'telegram','message':message,'requires_explicit_tool_action':True}; packet['verification']['status']='PASS'
        if write:
            ledger['entries'].append({'idempotency_key':key,'target':target,'chat_id':str(chat_id),'purpose':purpose,'message_digest':msg_digest,'status':'dry_run_ready','created_at':packet['created_at'],'created_ts':now_ts,'preview_only':True})
    else:
        if not send_result_path or not send_result_path.exists(): raise SystemExit('--send-result is required for record_send_result')
        send_result=_read_json(send_result_path)
        if not send_result.get('success') or str(send_result.get('chat_id'))!=str(chat_id): raise SystemExit('send result failed or chat_id mismatch')
        packet['status']='receipt_recorded'; packet['send_result']=send_result; packet['verification']['status']='PASS'; packet['verification']['checks'].append('send_result_validated')
        if write:
            ledger['entries'].append({'idempotency_key':key,'target':target,'chat_id':str(chat_id),'purpose':purpose,'message_digest':msg_digest,'status':'receipt_recorded','message_id':str(send_result.get('message_id')),'created_at':packet['created_at'],'created_ts':now_ts,'preview_only':False})
    art=_write_json(output_dir/f'{key}.telegram_notification_policy_packet.json',packet)
    if write:
        ledger_art=_write_json(ledger_path,ledger)
        packet['ledger_artifact']=ledger_art
        if orchestrator:
            task=_find_task(orchestrator,task_id); fm=task.fm
            fm['workflow_telegram_notification_policy_id']=key
            fm['workflow_telegram_notification_policy_status']=packet['status']
            fm['workflow_telegram_notification_policy_record']=packet
            ev=list(fm.get('evidence') or [])
            for marker in [f'artifact:telegram_notification_policy_packet:{art["sha256"]}',f'artifact:telegram_notification_ledger:{ledger_art["sha256"]}','proof:telegram_notification_policy_enforced','classification:workflow_kernel_telegram_policy_ready']:
                if marker not in ev: ev.append(marker)
            fm['evidence']=ev; orchestrator.save_task(task); packet['derived_refresh']=orchestrator.refresh_derived_task_views('workflow_runtime_telegram_notification_policy')
            _write_json(output_dir/f'{key}.telegram_notification_policy_packet.json',packet)
    packet['packet_artifact']=art
    return packet

def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--policy',type=Path,default=DEFAULT_POLICY); ap.add_argument('--ledger',type=Path,default=DEFAULT_LEDGER); ap.add_argument('--output-dir',type=Path,default=DEFAULT_OUTPUT_DIR)
    ap.add_argument('--target',default='telegram'); ap.add_argument('--chat-id',default='5524172309'); ap.add_argument('--purpose',default='system_test'); ap.add_argument('--message',default='')
    ap.add_argument('--mode',choices=['dry_run','record_send_result'],default='dry_run'); ap.add_argument('--send-result',type=Path); ap.add_argument('--write',action='store_true'); ap.add_argument('--task-id',default=DEFAULT_TASK_ID); ap.add_argument('--orchestrator',type=Path,default=ORCHESTRATOR_PATH); ap.add_argument('--json',action='store_true',dest='as_json')
    a=ap.parse_args(argv); orch=load_orchestrator(a.orchestrator) if a.write else None
    res=compile_request(policy_path=a.policy,ledger_path=a.ledger,output_dir=a.output_dir,target=a.target,chat_id=a.chat_id,purpose=a.purpose,message=a.message,mode=a.mode,send_result_path=a.send_result,write=a.write,orchestrator=orch,task_id=a.task_id)
    if a.as_json or not a.write: print(json.dumps(res,indent=2,ensure_ascii=False,sort_keys=True))
    else: print(res['packet_artifact']['path'])
    return 0
if __name__=='__main__': raise SystemExit(main())
