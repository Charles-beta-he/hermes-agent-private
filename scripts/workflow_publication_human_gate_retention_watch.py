#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-human-gate-retention-watch')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_human_gate_retention_watch_20260613_phase89.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_human_gate_retention_watch_20260613_phase89.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_watch(root:Path)->dict[str,Any]:
    gate_path=root/'docs/private/workflow-kernel-publication-apply-reopen-human-gate-packet/workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json'
    gate=read(gate_path)
    checks={'gate_ready':gate.get('status')=='PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_READY','default_keep_closed':gate.get('recommended_default')=='A_KEEP_CLOSED_NO_REOPEN','selected_option_null':gate.get('selected_option') is None,'packet_only':gate.get('packet_only') is True,'human_gate_required':gate.get('human_gate_required_for_apply') is True,'apply_disabled':gate.get('publication_apply_enabled') is False}
    drift=[k for k,v in checks.items() if not v]
    r={'schema':'workflow_kernel_publication_human_gate_retention_watch_v1','phase':89,'status':'PUBLICATION_HUMAN_GATE_RETENTION_WATCH_CLEAR' if not drift else 'PUBLICATION_HUMAN_GATE_RETENTION_WATCH_DRIFT','created_at':now(),'checks':checks,'drift':drift,'input_refs':[{'name':'phase88_reopen_human_gate','path':str(gate_path.relative_to(root)),'sha256':sha(gate_path),'status':gate.get('status')}],'retention_policy':'human_gate_packet_retained_fail_closed_no_apply','read_only':True,'packet_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Phase90 may compile read-only final index'}
    r['retention_digest']=digest({k:r[k] for k in r if k!='retention_digest'})
    return r
def write_report(r):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Human Gate Retention Watch — Phase 89\n\nStatus: **{r['status']}**\nDrift count: `{len(r['drift'])}`\nDigest: `{r['retention_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); r=build_watch(Path.cwd())
    if a.write_report: write_report(r)
    if a.json: print(json.dumps(r,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if r['status'].endswith('CLEAR') else 2
if __name__=='__main__': raise SystemExit(main())
