from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_final_index_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-governance-final-index/workflow_kernel_publication_governance_final_index_20260613_phase90.json',{'status':'PUBLICATION_GOVERNANCE_FINAL_INDEX_READY','read_only':True,'item_count':8,'publication_apply_enabled':apply,'external_publication_enabled':False})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_FINAL_INDEX_RETENTION_WATCH_CLEAR'; assert r['drift']==[]
def test_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_FINAL_INDEX_RETENTION_WATCH_DRIFT'; assert 'apply_disabled' in r['drift']
