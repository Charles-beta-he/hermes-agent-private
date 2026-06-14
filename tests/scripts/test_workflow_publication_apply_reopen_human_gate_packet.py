from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_apply_reopen_human_gate_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, drift=None):
    write(root,'docs/private/workflow-kernel-publication-closure-retention-watch/workflow_kernel_publication_closure_retention_watch_20260613_phase87.json',{'status':'PUBLICATION_CLOSURE_RETENTION_WATCH_CLEAR','drift':drift or [],'read_only':True,'publication_apply_enabled':False})
def test_ready_fail_closed(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path); assert p['status']=='PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_READY'; assert p['recommended_default']=='A_KEEP_CLOSED_NO_REOPEN'; assert p['packet_only'] is True; assert p['publication_apply_enabled'] is False
def test_blocks_on_retention_drift(tmp_path):
    m=load(); seed(tmp_path, ['x']); p=m.build_packet(tmp_path); assert p['status']=='PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_BLOCKED'; assert 'closure_no_drift' in p['blockers']
