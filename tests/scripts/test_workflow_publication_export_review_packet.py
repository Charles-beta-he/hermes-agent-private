from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_export_review_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, status='PUBLICATION_ARCHIVE_RETENTION_WATCH_CLEAR'):
    write(root,'docs/private/workflow-kernel-publication-archive-retention-watch/workflow_kernel_publication_archive_retention_watch_20260613_phase81.json',{'status':status,'read_only':True,'publication_apply_enabled':False})
def test_ready(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path); assert p['status']=='PUBLICATION_EXPORT_REVIEW_PACKET_READY'; assert p['packet_only'] is True; assert p['publication_apply_enabled'] is False
def test_blocked(tmp_path):
    m=load(); seed(tmp_path,'PUBLICATION_ARCHIVE_RETENTION_WATCH_DRIFT'); p=m.build_packet(tmp_path); assert p['status']=='PUBLICATION_EXPORT_REVIEW_PACKET_BLOCKED'; assert 'retention_clear' in p['blockers']
