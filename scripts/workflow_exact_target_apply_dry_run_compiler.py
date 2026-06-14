#!/usr/bin/env python3
"""Phase 73: exact-target apply dry-run compiler.

Compiles a dry-run preview from the Phase72 exact-target gate without selecting a
real external target or executing publication. No send, no publication, no cron
mutation, no credential access.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-exact-target-apply-dry-run-compiler')
REPORT_JSON=REPORT_DIR/'workflow_kernel_exact_target_apply_dry_run_compiler_20260613_phase73.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_exact_target_apply_dry_run_compiler_20260613_phase73.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_preview(root:Path)->dict[str,Any]:
    gate=read(root/'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json')
    archive=read(root/'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json')
    checks={
      'gate_ready':gate.get('status')=='PUBLICATION_EXACT_TARGET_GATE_READY',
      'gate_only':gate.get('gate_only') is True,
      'gate_apply_disabled':gate.get('publication_apply_enabled') is False,
      'archive_ready':archive.get('status')=='MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY',
      'archive_private_read_only':archive.get('read_only') is True,
    }
    blockers=[k for k,v in checks.items() if not v]
    preview={'target_adapter':None,'target_id':None,'payload_hash':None,'operator_reason':None,'rollback_plan_required':True,'would_publish':False,'would_send':False,'validation_mode':'schema_and_gate_only'}
    r={'schema':'workflow_kernel_exact_target_apply_dry_run_compiler_v1','phase':73,'status':'EXACT_TARGET_APPLY_DRY_RUN_COMPILER_READY' if not blockers else 'EXACT_TARGET_APPLY_DRY_RUN_COMPILER_BLOCKED','created_at':now(),'checks':checks,'blockers':blockers,'dry_run_preview':preview,'compiled_from_gate_digest':gate.get('gate_digest'),'compile_mode':'no_target_no_payload_preview_only','dry_run_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase74 authorization packet; still no publication apply'}
    r['compiler_digest']=digest({k:r[k] for k in r if k!='compiler_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Exact-target Apply Dry-run Compiler — Phase 73','',f"Status: **{r['status']}**",f"Compile mode: `{r['compile_mode']}`",f"Dry-run only: `{r['dry_run_only']}`",f"Compiler digest: `{r['compiler_digest']}`",'',f"Would publish: `{r['dry_run_preview']['would_publish']}`",f"Would send: `{r['dry_run_preview']['would_send']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_preview(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
