#!/usr/bin/env python3
"""Phase 70: governance archive index publication packet.

Packet-only gate before any external publication/sharing of the manual-only
governance archive. Does not publish, send, or mutate scheduler state.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-archive-publication-packet')
REPORT_JSON=REPORT_DIR/'workflow_kernel_archive_publication_packet_20260613_phase70.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_archive_publication_packet_20260613_phase70.md'
SCHEMA_JSON=REPORT_DIR/'workflow_kernel_archive_publication_schema_20260613_phase70.json'
DEFAULT='A_KEEP_PRIVATE_NO_PUBLICATION'
OPTIONS=[
 {'option':'A_KEEP_PRIVATE_NO_PUBLICATION','mode':'private_archive_only','side_effect':'none','authority':'none'},
 {'option':'B_PREPARE_REDACTED_INTERNAL_SUMMARY','mode':'redacted_summary_packet','side_effect':'local_file_only','authority':'review_required'},
 {'option':'C_PUBLISH_TO_EXTERNAL_TARGET','mode':'external_publication_apply','side_effect':'external_delivery','authority':'fresh_exact_target_payload_required'},
]
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_packet(root:Path)->dict[str,Any]:
    archive=read(root/'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json')
    health=read(root/'docs/private/workflow-kernel-periodic-closure-health-report/workflow_kernel_periodic_closure_health_report_20260613_phase69.json')
    checks={'archive_ready':archive.get('status')=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY','health_clear':health.get('status')=='PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR','health_stage_report_only':health.get('stage_report_only') is True}
    blockers=[k for k,v in checks.items() if not v]
    p={'schema':'workflow_kernel_archive_publication_packet_v1','phase':70,'status':'ARCHIVE_PUBLICATION_PACKET_READY' if not blockers else 'ARCHIVE_PUBLICATION_PACKET_BLOCKED','created_at':now(),'recommended_default':DEFAULT,'selected_option':None,'publication_options':OPTIONS,'checks':checks,'blockers':blockers,'packet_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'unattended_dispatch_enabled':False,'external_side_effects_executed':False,'cron_state_mutated':False,'message_sent':False,'credential_accessed':False,'next_boundary':'Stop at private archive unless a future exact publication target/payload is authorized'}
    p['packet_digest']=digest({k:p[k] for k in p if k!='packet_digest'})
    return p
def write_packet(p):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    SCHEMA_JSON.write_text(json.dumps({'type':'object','required':['option','target','payload','operator_reason'],'properties':{'option':{'enum':[x['option'] for x in OPTIONS]},'target':{'type':'string'},'payload':{'type':'string'},'operator_reason':{'type':'string'}}},ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Archive Publication Packet — Phase 70','',f"Status: **{p['status']}**",f"Default: `{p['recommended_default']}`",f"Packet digest: `{p['packet_digest']}`",f"Publication apply enabled: `{p['publication_apply_enabled']}`",f"External side effects: `{p['external_side_effects_executed']}`"] )+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); args=ap.parse_args(argv)
    p=build_packet(Path.cwd())
    if args.write_report: write_packet(p)
    if args.json: print(json.dumps(p,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if p['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
