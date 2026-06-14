#!/usr/bin/env python3
"""Phase 74: publication apply authorization packet.

Creates an explicit authorization packet for any future publication apply. This
phase does not publish, send, mutate cron, or access credentials.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-apply-authorization-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_apply_authorization_packet_20260613_phase74.md'
DECISION_SCHEMA=REPORT_DIR/'workflow_kernel_publication_apply_authorization_decision_schema_20260613_phase74.json'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    compiler=read(root/'docs/private/workflow-kernel-exact-target-apply-dry-run-compiler/workflow_kernel_exact_target_apply_dry_run_compiler_20260613_phase73.json')
    gate=read(root/'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json')
    checks={'compiler_ready':compiler.get('status')=='EXACT_TARGET_APPLY_DRY_RUN_COMPILER_READY','compiler_dry_run_only':compiler.get('dry_run_only') is True,'gate_ready':gate.get('status')=='PUBLICATION_EXACT_TARGET_GATE_READY','gate_apply_disabled':gate.get('publication_apply_enabled') is False}
    blockers=[k for k,v in checks.items() if not v]
    options=[
      {'id':'A_KEEP_PRIVATE_NO_PUBLICATION','label':'Keep archive private; no publication apply','apply_enabled':False},
      {'id':'B_AUTHORIZE_FILE_EXPORT_DRY_RUN_ONLY','label':'Authorize local file-export dry-run only','apply_enabled':False},
      {'id':'C_AUTHORIZE_EXACT_TARGET_PUBLICATION_APPLY_FUTURE','label':'Open a future exact-target apply phase after operator supplies full target/payload/rollback','apply_enabled':False},
    ]
    schema={'type':'object','required':['decision','operator_reason'],'properties':{'decision':{'enum':[o['id'] for o in options]},'operator_reason':{'type':'string','minLength':1},'exact_target_packet_ref':{'type':'string'},'rollback_acknowledged':{'type':'boolean'}}}
    p={'schema':'workflow_kernel_publication_apply_authorization_packet_v1','phase':74,'status':'PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY' if not blockers else 'PUBLICATION_APPLY_AUTHORIZATION_PACKET_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'options':options,'recommended_default':'A_KEEP_PRIVATE_NO_PUBLICATION','selected_option':None,'decision_schema':schema,'packet_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Future Phase75+ may remain private or prepare local export; real external publication still requires exact target/payload'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    DECISION_SCHEMA.write_text(json.dumps(p['decision_schema'],ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Apply Authorization Packet — Phase 74','',f"Status: **{p['status']}**",f"Recommended default: `{p['recommended_default']}`",f"Packet only: `{p['packet_only']}`",f"Packet digest: `{p['packet_digest']}`",'','## Options',*[f"- `{o['id']}` — {o['label']}" for o in p['options']]])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    p=build_packet(Path.cwd())
    if a.write_report: write_packet(p)
    if a.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
