from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_governance_final_index.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    rels=['workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json','workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json','workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json','workflow-kernel-publication-export-review-packet/workflow_kernel_publication_export_review_packet_20260613_phase82.json','workflow-kernel-publication-apply-final-boundary-packet/workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json','workflow-kernel-publication-governance-closure-summary/workflow_kernel_publication_governance_closure_summary_20260613_phase86.json','workflow-kernel-publication-apply-reopen-human-gate-packet/workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json','workflow-kernel-publication-human-gate-retention-watch/workflow_kernel_publication_human_gate_retention_watch_20260613_phase89.json']
    for rel in rels: write(root,'docs/private/'+rel,{'status':'OK','publication_apply_enabled':False})
def test_ready(tmp_path):
    m=load(); seed(tmp_path); idx=m.build_index(tmp_path); assert idx['status']=='PUBLICATION_GOVERNANCE_FINAL_INDEX_READY'; assert idx['item_count']==8; assert idx['publication_apply_enabled'] is False
def test_degraded_when_missing(tmp_path):
    m=load(); idx=m.build_index(tmp_path); assert idx['status']=='PUBLICATION_GOVERNANCE_FINAL_INDEX_DEGRADED'; assert idx['missing']
