from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_archive_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-governance-archive/workflow_kernel_publication_governance_archive_20260613_phase80.json',{'status':'PUBLICATION_GOVERNANCE_ARCHIVE_READY','read_only':True,'publication_apply_enabled':apply})
    write(root,'docs/private/workflow-kernel-publication-closure-health-report/workflow_kernel_publication_closure_health_report_20260613_phase79.json',{'status':'PUBLICATION_CLOSURE_HEALTH_REPORT_CLEAR','publication_apply_enabled':False})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR'; assert r['drift']==[]; assert r['read_only'] is True
def test_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_ARCHIVE_RETENTION_WATCH_DRIFT'; assert 'archive_no_apply' in r['drift']
