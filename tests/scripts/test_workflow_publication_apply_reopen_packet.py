from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_apply_reopen_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, status='PRIVATE_EXPORT_RETENTION_WATCH_CLEAR'):
    write(root,'docs/private/workflow-kernel-private-export-retention-watch/workflow_kernel_private_export_retention_watch_20260613_phase77.json',{'status':status})
    write(root,'docs/private/workflow-kernel-publication-apply-implementation-choice/workflow_kernel_publication_apply_implementation_choice_20260613_phase76.json',{'choice':'A_KEEP_PRIVATE_NO_APPLY','publication_apply_enabled':False})
def test_reopen_packet_ready_fail_closed(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_REOPEN_PACKET_READY'
    assert p['recommended_default']=='A_NO_REOPEN_KEEP_PRIVATE'
    assert p['packet_only'] is True
    assert p['reopen_apply_enabled'] is False
    assert p['publication_apply_enabled'] is False
def test_reopen_packet_blocks_on_retention_drift(tmp_path):
    m=load(); seed(tmp_path,'PRIVATE_EXPORT_RETENTION_WATCH_DRIFT'); p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_REOPEN_PACKET_BLOCKED'
    assert 'retention_clear' in p['blockers']
