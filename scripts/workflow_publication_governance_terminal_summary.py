#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
REPORT_DIR=Path('docs/private/workflow-kernel-publication-governance-terminal-summary')
REPORT_JSON=REPORT_DIR/'workflow_kernel_publication_governance_terminal_summary_20260613_phase92.json'
REPORT_MD=REPORT_DIR/'workflow_kernel_publication_governance_terminal_summary_20260613_phase92.md'
def now(): return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace('+00:00','Z')
def read(p:Path): return json.loads(p.read_text()) if p.exists() else {}
def sha(p:Path): return hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
def digest(o:Any): return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def build_summary(root:Path)->dict[str,Any]:
    entries=[('phase90_final_index','docs/private/workflow-kernel-publication-governance-final-index/workflow_kernel_publication_governance_final_index_20260613_phase90.json'),('phase91_final_index_retention','docs/private/workflow-kernel-publication-final-index-retention-watch/workflow_kernel_publication_final_index_retention_watch_20260613_phase91.json')]
    items=[]; missing=[]; drift=[]
    for name,rel in entries:
        p=root/rel; d=read(p)
        if not p.exists(): missing.append(name)
        if d.get('publication_apply_enabled') not in (False, None): drift.append(name)
        items.append({'name':name,'path':rel,'sha256':sha(p),'status':d.get('status'),'publication_apply_enabled':d.get('publication_apply_enabled')})
    blockers=missing+drift
    s={'schema':'workflow_kernel_publication_governance_terminal_summary_v1','phase':92,'status':'PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_READY' if not blockers else 'PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_DEGRADED','created_at':now(),'summary_policy':'read_only_terminal_summary_closed_no_apply','items':items,'item_count':len(items),'missing':missing,'apply_drift':drift,'terminal_state':'closed_no_apply_human_gated_reopen_only','read_only':True,'terminal_summary_only':True,'publication_apply_enabled':False,'external_publication_enabled':False,'external_side_effects_executed':False,'unattended_dispatch_enabled':False,'cron_state_mutated':False,'credential_accessed':False,'message_sent':False,'next_boundary':'Terminal publication governance summary complete; future real apply requires explicit human exact target/payload/rollback'}
    s['summary_digest']=digest({k:s[k] for k in s if k!='summary_digest'})
    return s
def write_summary(s):
    REPORT_DIR.mkdir(parents=True,exist_ok=True); REPORT_JSON.write_text(json.dumps(s,ensure_ascii=False,indent=2,sort_keys=True)+'\n'); REPORT_MD.write_text(f"# Workflow Kernel Publication Governance Terminal Summary — Phase 92\n\nStatus: **{s['status']}**\nTerminal state: `{s['terminal_state']}`\nDigest: `{s['summary_digest']}`\n")
def main(argv=None):
    ap=argparse.ArgumentParser(); ap.add_argument('--write-report',action='store_true'); ap.add_argument('--json',action='store_true'); a=ap.parse_args(argv); s=build_summary(Path.cwd())
    if a.write_report: write_summary(s)
    if a.json: print(json.dumps(s,ensure_ascii=False,indent=2,sort_keys=True))
    return 0 if s['status'].endswith('READY') else 2
if __name__=='__main__': raise SystemExit(main())
