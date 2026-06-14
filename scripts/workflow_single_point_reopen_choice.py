#!/usr/bin/env python3
"""Phase 67: single-point reopen implementation choice.

Records the safe-default choice for the concrete reopen implementation point:
keep the ladder closed. Record-only; no apply, no unattended dispatch, no cron
mutation, no message send.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPORT_DIR=Path('docs/private/workflow-kernel-single-point-reopen-choice')
REPORT_JSON=REPORT_DIR/'workflow_kernel_single_point_reopen_choice_20260613_phase67.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_single_point_reopen_choice_20260613_phase67.md'
LEDGER=REPORT_DIR/'workflow_kernel_reopen_choices.jsonl'
SAFE_CHOICE='A_NO_REOPEN_KEEP_MANUAL_ONLY'

def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read_json(p:Path)->dict[str,Any]: return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any)->str: return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

def build_choice(root:Path, choice:str=SAFE_CHOICE, reason:str='safe default: keep manual-only closure') -> dict[str,Any]:
    packet=read_json(root/'docs/private/workflow-kernel-future-unattended-reopen-packet/workflow_kernel_future_unattended_reopen_packet_20260613_phase66.json')
    options=[x.get('option') for x in packet.get('reopen_options',[]) if isinstance(x,dict)]
    checks={
      'phase66_packet_ready': packet.get('status')=='FUTURE_UNATTENDED_REOPEN_PACKET_READY',
      'choice_allowed': choice in options,
      'packet_apply_disabled': packet.get('reopen_apply_enabled') is False,
      'packet_unattended_disabled': packet.get('unattended_dispatch_enabled') is False,
    }
    blockers=[k for k,v in checks.items() if not v]
    rec={
      'schema':'workflow_kernel_single_point_reopen_choice_v1','phase':67,
      'status':'SINGLE_POINT_REOPEN_CHOICE_RECORDED' if not blockers else 'SINGLE_POINT_REOPEN_CHOICE_BLOCKED',
      'created_at':now(),'choice':choice,'reason':reason,'phase66_packet_digest':packet.get('packet_digest'),
      'checks':checks,'blockers':blockers,'record_only':True,'selected_option':choice,
      'reopen_apply_enabled':False,'unattended_dispatch_enabled':False,'external_side_effects_executed':False,
      'cron_state_mutated':False,'message_sent':False,
      'next_boundary':'Phase68 manual-only governance archive',
    }
    rec['choice_digest']=digest({k:rec[k] for k in rec if k!='choice_digest'})
    return rec

def write_choice(rec:dict[str,Any])->None:
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(rec,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    with LEDGER.open('a',encoding='utf-8') as f: f.write(json.dumps(rec,ensure_ascii=False,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Single-point Reopen Choice — Phase 67','',f"Status: **{rec['status']}**",f"Choice: `{rec['choice']}`",f"Choice digest: `{rec['choice_digest']}`",f"Reopen apply enabled: `{rec['reopen_apply_enabled']}`",f"Unattended dispatch enabled: `{rec['unattended_dispatch_enabled']}`"] )+'\n')

def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--choice',default=SAFE_CHOICE); ap.add_argument('--reason',default='safe default: keep manual-only closure'); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    rec=build_choice(Path.cwd(),a.choice,a.reason)
    if a.write_report: write_choice(rec)
    if a.json: print(json.dumps(rec,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if rec['status'].endswith('RECORDED') else 2
if __name__=='__main__': raise SystemExit(main())
