#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-apply-reopen-human-gate-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.md'
SCHEMA_JSON=REPORT_DIR/'workflow_kernel_publication_apply_reopen_human_gate_decision_schema_20260613_phase88.json'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    watch=read(root/'docs/private/workflow-kernel-publication-closure-retention-watch/workflow_kernel_publication_closure_retention_watch_20260613_phase87.json')
    checks={'closure_retention_clear':watch.get('status')=='PUBLICATION_CLOSURE_RETENTION_WATCH_CLEAR','closure_no_drift':watch.get('drift')==[],'closure_read_only':watch.get('read_only') is True,'apply_disabled':watch.get('publication_apply_enabled') is False}
    blockers=[k for k,v in checks.items() if not v]
    options=[{'id':'A_KEEP_CLOSED_NO_REOPEN','label':'Keep publication apply closed','requires_human':False,'apply_enabled':False},{'id':'B_REQUEST_EXACT_TARGET_REOPEN','label':'Human supplies exact target/payload/rollback for a future apply design','requires_human':True,'apply_enabled':False},{'id':'C_REQUEST_EXTERNAL_PUBLICATION_TARGET','label':'Human supplies external publication target and blast-radius packet','requires_human':True,'apply_enabled':False}]
    decision_schema={'type':'object','required':['option','operator_reason'],'properties':{'option':{'enum':[o['id'] for o in options]},'operator_reason':{'type':'string','minLength':1},'target_adapter':{'type':'string'},'target_id':{'type':'string'},'payload_digest':{'type':'string'},'rollback_plan':{'type':'string'},'blast_radius':{'type':'string'}}}
    p={'schema':'workflow_kernel_publication_apply_reopen_human_gate_packet_v1','phase':88,'status':'PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_READY' if not blockers else 'PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'options':options,'decision_schema':decision_schema,'recommended_default':'A_KEEP_CLOSED_NO_REOPEN','selected_option':None,'packet_only':True,'human_gate_required_for_apply':True,'reopen_apply_enabled':False,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase89+ may continue read-only governance retention; real reopen requires human exact target/payload/rollback choice'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    SCHEMA_JSON.write_text(json.dumps(p['decision_schema'],ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text(f"# Workflow Kernel Publication Apply Reopen Human Gate Packet — Phase 88\n\nStatus: **{p['status']}**\nRecommended default: `{p['recommended_default']}`\nPacket only: `{p['packet_only']}`\nDigest: `{p['packet_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); p=build_packet(Path.cwd())
    if a.write_report: write_packet(p)
    if a.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
