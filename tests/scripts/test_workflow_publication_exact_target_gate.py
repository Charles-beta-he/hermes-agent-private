from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_exact_target_gate.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, retention='PRIVATE_ARCHIVE_RETENTION_WATCH_CLEAR'):
    write(root,'docs/private/workflow-kernel-private-archive-retention-watch/workflow_kernel_private_archive_retention_watch_20260613_phase71.json',{'status':retention})
    write(root,'docs/private/workflow-kernel-archive-publication-packet/workflow_kernel_archive_publication_packet_20260613_phase70.json',{'status':'ARCHIVE_PUBLICATION_PACKET_READY','recommended_default':'A_KEEP_PRIVATE_NO_PUBLICATION'})
def test_gate_ready_without_apply(tmp_path):
    m=load(); seed(tmp_path); g=m.build_gate(tmp_path)
    assert g['status']=='PUBLICATION_EXACT_TARGET_GATE_READY'
    assert g['gate_only'] is True
    assert g['publication_apply_enabled'] is False
    assert 'target_adapter' in g['request_schema']['required']
def test_gate_blocks_on_retention_drift(tmp_path):
    m=load(); seed(tmp_path,'PRIVATE_ARCHIVE_RETENTION_WATCH_DRIFT'); g=m.build_gate(tmp_path)
    assert g['status']=='PUBLICATION_EXACT_TARGET_GATE_BLOCKED'
    assert 'retention_clear' in g['blockers']
