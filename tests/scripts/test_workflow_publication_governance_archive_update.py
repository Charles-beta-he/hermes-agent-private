from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_governance_archive_update.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root):
    rels=['workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json','workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json','workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json','workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json','workflow-kernel-publication-apply-reopen-packet/workflow_kernel_publication_apply_reopen_packet_20260613_phase78.json','workflow-kernel-publication-closure-health-report/workflow_kernel_publication_closure_health_report_20260613_phase79.json']
    for rel in rels: write(root,'docs/private/'+rel,{'status':'OK','publication_apply_enabled':False})
def test_archive_ready(tmp_path):
    m=load(); seed(tmp_path); a=m.build_archive(tmp_path)
    assert a['status']=='PUBLICATION_GOVERNANCE_ARCHIVE_READY'
    assert a['item_count']==6
    assert a['read_only'] is True
    assert a['publication_apply_enabled'] is False
def test_archive_degraded_when_missing(tmp_path):
    m=load(); a=m.build_archive(tmp_path)
    assert a['status']=='PUBLICATION_GOVERNANCE_ARCHIVE_DEGRADED'
    assert a['missing']
