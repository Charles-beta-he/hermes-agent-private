from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_apply_final_boundary_packet.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, selected=None):
    write(root,'docs/private/workflow-kernel-publication-export-review-retention-watch/workflow_kernel_publication_export_review_retention_watch_20260613_phase83.json',{'status':'PUBLICATION_EXPORT_REVIEW_RETENTION_WATCH_CLEAR','packet_only':True})
    write(root,'docs/private/workflow-kernel-publication-exact-target-gate/workflow_kernel_publication_exact_target_gate_20260613_phase72.json',{'status':'PUBLICATION_EXACT_TARGET_GATE_READY','selected_target':selected})
def test_ready_fail_closed(tmp_path):
    m=load(); seed(tmp_path); p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_READY'
    assert p['recommended_default']=='A_STOP_AT_PRIVATE_REVIEW'
    assert p['packet_only'] is True
    assert p['publication_apply_enabled'] is False
    assert p['exact_target_present'] is False
def test_blocks_if_target_already_selected_without_apply_phase(tmp_path):
    m=load(); seed(tmp_path, 'telegram') ; p=m.build_packet(tmp_path)
    assert p['status']=='PUBLICATION_APPLY_FINAL_BOUNDARY_PACKET_BLOCKED'
    assert 'exact_target_absent' in p['blockers']
