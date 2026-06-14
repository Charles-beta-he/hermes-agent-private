from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_export_review_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-export-review-packet/workflow_kernel_publication_export_review_packet_20260613_phase82.json',{'status':'PUBLICATION_EXPORT_REVIEW_PACKET_READY','packet_only':True,'export_apply_enabled':apply,'publication_apply_enabled':False})
    write(root,'docs/private/workflow-kernel-publication-archive-retention-watch/workflow_kernel_publication_archive_retention_watch_20260613_phase81.json',{'status':'PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR'})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path)
    assert r['status']=='PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_CLEAR'
    assert r['drift']==[]
    assert r['packet_only'] is True
    assert r['export_apply_enabled'] is False
def test_detects_export_apply_drift(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_watch(tmp_path)
    assert r['status']=='PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_DRIFT'
    assert 'review_no_export_apply' in r['drift']
