from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_governance_terminal_summary.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    write(root,'docs/private/workflow-kernel-publication-governance-final-index/workflow_kernel_publication_governance_final_index_20260613_phase90.json',{'status':'OK','publication_apply_enabled':False})
    write(root,'docs/private/workflow-kernel-publication-final-index-retention-watch/workflow_kernel_publication_final_index_retention_watch_20260613_phase91.json',{'status':'OK','publication_apply_enabled':False})
def test_ready(tmp_path):
    m=load(); seed(tmp_path); s=m.build_summary(tmp_path); assert s['status']=='PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_READY'; assert s['terminal_state']=='closed_no_apply_human_gated_reopen_only'; assert s['publication_apply_enabled'] is False
def test_degraded_missing(tmp_path):
    m=load(); s=m.build_summary(tmp_path); assert s['status']=='PUBLICATION_GOVERNANCE_TERMINAL_SUMMARY_DEGRADED'; assert s['missing']
