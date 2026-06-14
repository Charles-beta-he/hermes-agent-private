#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-apply-final-boundary-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.md'
SCHEMA_JSON=REPORT_DIR/'workflow_kernel_publication_apply_final_boundary_decision_schema_20260613_phase84.json'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    watch=read(root/'docs/private/workflow-kernel-publication-export-review-retention-watch/workflow_kernel_publication_export_review_retention_watch_20260613_phase83.json')
    exact_gate=read(root/'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json')
    checks={'review_retention_clear':watch.get('status')=='PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_CLEAR','review_packet_only':watch.get('packet_only') is True,'exact_gate_ready':exact_gate.get('status')=='PUBLICATION_EXACT_TARGET_GATE_READY','exact_target_absent':exact_gate.get('selected_target') in (None, '')}
    blockers=[k for k,v in checks.items() if not v]
    options=[
      {'id':'A_STOP_AT_PRIVATE_REVIEW','label':'Stop at private review / no apply','requires_exact_target':False,'apply_enabled':False},
      {'id':'B_PREPARE_EXACT_TARGET_APPLY_DESIGN','label':'Prepare design only for exact-target apply','requires_exact_target':True,'apply_enabled':False},
      {'id':'C_REQUEST_HUMAN_EXACT_TARGET_FOR_APPLY','label':'Request human-selected target/payload/rollback before any apply','requires_exact_target':True,'apply_enabled':False},
    ]
    decision_schema={'type':'object','required':['option','operator_reason'],'properties':{'option':{'enum':[o['id'] for o in options]},'operator_reason':{'type':'string','minLength':1},'target_adapter':{'type':'string'},'target_id':{'type':'string'},'payload_digest':{'type':'string'},'rollback_plan':{'type':'string'}}}
    p={'schema':'workflow_kernel_publication_apply_final_boundary_packet_v1','phase':84,'status':'PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_READY' if not blockers else 'PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'options':options,'decision_schema':decision_schema,'recommended_default':'A_STOP_AT_PRIVATE_REVIEW','selected_option':None,'packet_only':True,'final_boundary':True,'exact_target_present':False,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase85+ may continue read-only closure; real apply requires exact target/payload/rollback human selection'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    SCHEMA_JSON.write_text(json.dumps(p['decision_schema'],ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text(f"# Workflow Kernel Publication Apply Final Boundary Packet — Phase 84\n\nStatus: **{p['status']}**\nRecommended default: `{p['recommended_default']}`\nPacket only: `{p['packet_only']}`\nDigest: `{p['packet_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    p=build_packet(Path.cwd())
    if a.write_report: write_packet(p)
    if a.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
