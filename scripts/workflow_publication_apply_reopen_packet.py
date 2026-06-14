#!/usr/bin/env python3
"""Phase 78: publication apply reopen packet.

Defines future reopen options for publication apply. Packet-only; no apply.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-apply-reopen-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_apply_reopen_packet_20260613_phase78.md'
DECISION_SCHEMA=REPORT_DIR/'workflow_kernel_publication_apply_reopen_decision_schema_20260613_phase78.json'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    watch=read(root/'docs/private/workflow-kernel-private-export-retention-watch/workflow_kernel_private_export_retention_watch_20260613_phase77.json')
    choice=read(root/'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json')
    checks={'retention_clear':watch.get('status')=='PRIVATE_EXPORT_RETENTION_WATCH_CLEAR','choice_keep_private':choice.get('choice')=='A_KEEP_PRIVATE_NO_APPLY','choice_no_apply':choice.get('publication_apply_enabled') is False}
    blockers=[k for k,v in checks.items() if not v]
    options=[
      {'id':'A_NO_REOPEN_KEEP_PRIVATE','label':'Keep private/no-apply; default','apply_enabled':False},
      {'id':'B_REOPEN_LOCAL_EXPORT_REVIEW_ONLY','label':'Prepare a local export review packet only','apply_enabled':False},
      {'id':'C_REOPEN_EXACT_TARGET_PUBLICATION_APPLY_DESIGN','label':'Design future exact-target external publication apply; requires target/payload/rollback','apply_enabled':False},
    ]
    schema={'type':'object','required':['option','operator_reason'],'properties':{'option':{'enum':[o['id'] for o in options]},'operator_reason':{'type':'string','minLength':1},'exact_target_required':{'const':True}}}
    p={'schema':'workflow_kernel_publication_apply_reopen_packet_v1','phase':78,'status':'PUBLICATION_APPLY_REOPEN_PACKET_READY' if not blockers else 'PUBLICATION_APPLY_REOPEN_PACKET_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'options':options,'recommended_default':'A_NO_REOPEN_KEEP_PRIVATE','selected_option':None,'decision_schema':schema,'packet_only':True,'reopen_apply_enabled':False,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase79+ may continue read-only review; real apply requires exact-target choice'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    DECISION_SCHEMA.write_text(json.dumps(p['decision_schema'],ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Apply Reopen Packet — Phase 78','',f"Status: **{p['status']}**",f"Recommended default: `{p['recommended_default']}`",f"Packet only: `{p['packet_only']}`",f"Packet digest: `{p['packet_digest']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    p=build_packet(Path.cwd())
    if a.write_report: write_packet(p)
    if a.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
