from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_final_boundary_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-apply-final-boundary-packet/workflow_kernel_publication_apply_final_boundary_packet_20260613_phase84.json',{'status':'PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_READY','recommended_default':'A_STOP_AT_PRIVATE_REVIEW','selected_option':None,'packet_only':True,'final_boundary':True,'publication_apply_enabled':apply})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_FINAL_BOUNDARY_RETENTION_WATCH_CLEAR'; assert r['drift']==[]; assert r['publication_apply_enabled'] is False
def test_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_FINAL_BOUNDARY_RETENTION_WATCH_DRIFT'; assert 'apply_disabled' in r['drift']
