from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_closure_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-governance-closure-summary/workflow_kernel_publication_governance_closure_summary_20260613_phase86.json',{'status':'PUBLICATION_GOVERNANCE_CLOSURE_SUMMARY_READY','read_only':True,'item_count':6,'publication_apply_enabled':apply,'external_publication_enabled':False})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_CLOSURE_RETENTION_WATCH_CLEAR'; assert r['drift']==[]; assert r['read_only'] is True
def test_detects_apply_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_CLOSURE_RETENTION_WATCH_DRIFT'; assert 'apply_disabled' in r['drift']
