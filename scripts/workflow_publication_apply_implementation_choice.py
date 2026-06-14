#!/usr/bin/env python3
"""Phase 76: publication apply implementation choice.

Records the implementation choice for publication apply. With no exact target or
payload present, safe default is keep private / no apply.
"""
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-apply-implementation-choice')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_apply_implementation_choice_20260613_phase76.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_choice(root:Path, choice:str='A_KEEP_PRIVATE_NO_APPLY')->dict[str,Any]:
    export=read(root/'docs/private/workflow-kernel-private-export-artifact-compiler/workflow_kernel_private_export_artifact_compiler_20260613_phase75.json')
    auth=read(root/'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json')
    choices={'A_KEEP_PRIVATE_NO_APPLY':'Keep private; do not open publication apply','B_PREPARE_LOCAL_FILE_EXPORT_ONLY':'Future local file export only; still no external publish','C_OPEN_EXACT_TARGET_PUBLICATION_APPLY':'Requires explicit target/payload; not selected by default'}
    valid=choice in choices
    exact_target_present=bool(auth.get('selected_option')) and auth.get('selected_option')=='C_AUTHORIZE_EXACT_TARGET_PUBLICATION_APPLY_FUTURE'
    blockers=[]
    if export.get('status')!='PRIVATE_EXPORT_ARTIFACT_COMPILER_READY': blockers.append('private_export_not_ready')
    if not valid: blockers.append('invalid_choice')
    if choice=='C_OPEN_EXACT_TARGET_PUBLICATION_APPLY' and not exact_target_present: blockers.append('exact_target_payload_absent')
    status='PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED' if not blockers else 'PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_BLOCKED'
    r={'schema':'workflow_kernel_publication_apply_implementation_choice_v1','phase':76,'status':status,'created_at':now(),'choice':choice,'choice_label':choices.get(choice),'available_choices':choices,'blockers':blockers,'record_only':True,'exact_target_present':exact_target_present,'selected_apply_scheme':None,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'No large divergence opened; continue private/no-apply maintenance unless exact target/payload is supplied'}
    r['choice_digest']=digest({k:r[k] for k in r if k!='choice_digest'})
    return r
def write_choice(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True)
    REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n')
    REPORT_MD.write_text('\n'.join(['# Workflow Kernel Publication Apply Implementation Choice — Phase 76','',f"Status: **{r['status']}**",f"Choice: `{r['choice']}`",f"Record only: `{r['record_only']}`",f"Publication apply enabled: `{r['publication_apply_enabled']}`",f"Choice digest: `{r['choice_digest']}`"])+'\n')
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--choice',default='A_KEEP_PRIVATE_NO_APPLY'); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv)
    r=build_choice(Path.cwd(), a.choice)
    if a.write_report: write_choice(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('RECORDED') else 2
if __name__=='__main__': raise SystemExit(main())
