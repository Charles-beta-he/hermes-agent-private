from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_private_archive_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',{'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY','read_only':True})
    write(root,'docs/private/workflow-kernel-periodic-closure-health-report/workflow_kernel_periodic_closure_health_report_20260613_phase69.json',{'status':'PERIODIC_CLOSURE_HEALTH_REPORT_CLEAR'})
    write(root,'docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json',{'status':'ARCHIVE_PUBLICATION_PACKET_READY','publication_apply_enabled':apply,'recommended_default':'A_KEEP_PRIVATE_NO_PUBLICATION'})
def test_retention_watch_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path)
    assert r['status']=='PRIVATE_ARCHIVE_RETENTION_WATCH_CLEAR'
    assert r['read_only'] is True
    assert r['publication_apply_enabled'] is False
    assert r['external_side_effects_executed'] is False
def test_retention_watch_detects_apply_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path)
    assert r['status']=='PRIVATE_ARCHIVE_RETENTION_WATCH_DRIFT'
    assert 'publication_apply_disabled' in r['drift']
