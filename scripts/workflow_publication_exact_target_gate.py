#!/usr/bin/env python3
"""Phase 72: publication apply exact-target gate.

Defines the exact target/payload gate for any future archive publication. This
phase remains gate-only: it does not publish, send, or enable external delivery.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-exact-target-gate')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_exact_target_gate_20260613_phase72.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_exact_target_gate_20260613_phase72.md'
SCHEMA_JSON=REPORT_DIR/'workflow_kernel_publication_exact_target_request_schema_20260613_phase72.json'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_gate(root:Path)->dict[str,Any]:
    retention=read(root/'docs/private/workflow-kernel-private-archive-retention-watch/workflow_kernel_private_archive_retention_watch_20260613_phase71.json')
    packet=read(root/'docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json')
    checks={'retention_clear':retention.get('status')=='PRIVATE_ARCHIVE_RETENTION_WATCH_CLEAR','phase70_packet_ready':packet.get('status')=='ARCHIVE_PUBLICATION_PACKET_READY','phase70_private_default':packet.get('recommended_default')=='A_KEEP_PRIVATE_NO_PUBLICATION'}
    blockers=[k for k,v in checks.items() if not v]
    schema={'type':'object','required':['target_adapter','target_id','payload','operator_reason','rollback_plan'],'properties':{'target_adapter':{'enum':['telegram','lark','github','file_export']},'target_id':{'type':'string','minLength':1},'payload':{'type':'string','minLength':1},'operator_reason':{'type':'string','minLength':1},'rollback_plan':{'type':'string','minLength':1},'idempotency_key':{'type':'string'}}}
    g={'schema':'workflow_kernel_publication_exact_target_gate_v1','phase':72,'status':'PUBLICATION_EXACT_TARGET_GATE_READY' if not blockers else 'PUBLICATION_EXACT_TARGET_GATE_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'request_schema':schema,'gate_only':True,'selected_target':None,'selected_payload_hash':None,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'A future apply phase must provide exact target_adapter,target_id,payload,operator_reason,rollback_plan'}
    g['gate_digest']=digest({k:g[k] for k in g if k!='gate_digest'})
    return g
def write_gate(g):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(g,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    SCHEMA_JSON.write_text(json.dumps(g['request_schema'],ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Exact-target Gate — Phase 72','',f"Status: **{g['status']}**",f"Gate only: `{g['gate_only']}`",f"Gate digest: `{g['gate_digest']}`",f"Publication apply enabled: `{g['publication_apply_enabled']}`"] )+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    g=build_gate(Path.cwd())
    if a.write_report: write_gate(g)
    if a.json: print(json.dumps(g,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if g['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
