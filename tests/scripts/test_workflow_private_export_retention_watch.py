from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_private_export_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-private-export-artifact-compiler/workflow_kernel_private_export_artifact_compiler_20260613_phase75.json',{'status':'PRIVATE_EXPORT_ARTIFACT_COMPILER_READY','export_manifest':{'visibility':'private_local_only'},'publication_apply_enabled':apply})
    write(root,'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json',{'status':'PUBLICATION_APPLY_IMPLEMENTATION_CHOICE_RECORDED','choice':'A_KEEP_PRIVATE_NO_APPLY','publication_apply_enabled':False})
def test_retention_watch_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path)
    assert r['status']=='PRIVATE_EXPORT_RETENTION_WATCH_CLEAR'
    assert r['drift']==[]
    assert r['read_only'] is True
    assert r['publication_apply_enabled'] is False
def test_retention_watch_detects_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path)
    assert r['status']=='PRIVATE_EXPORT_RETENTION_WATCH_DRIFT'
    assert 'export_no_apply' in r['drift']
