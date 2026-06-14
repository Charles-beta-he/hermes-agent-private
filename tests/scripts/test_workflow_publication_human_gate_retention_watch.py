from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_publication_human_gate_retention_watch.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root, rel, payload):
    p=root/rel; p.parent.mkdir(parents=True, exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, selected=None):
    write(root,'docs/private/workflow-kernel-publication-apply-reopen-human-gate-packet/workflow_kernel_publication_apply_reopen_human_gate_packet_20260613_phase88.json',{'status':'PUBLICATION_APPLY_REOPEN_HUMAN_GATE_PACKET_READY','recommended_default':'A_KEEP_CLOSED_NO_REOPEN','selected_option':selected,'packet_only':True,'human_gate_required_for_apply':True,'publication_apply_enabled':False})
def test_clear(tmp_path):
    m=load(); seed(tmp_path); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_HUMAN_GATE_RETENTION_WATCH_CLEAR'; assert r['drift']==[]
def test_drift(tmp_path):
    m=load(); seed(tmp_path, 'B_REQUEST_EXACT_TARGET_REOPEN'); r=m.build_watch(tmp_path); assert r['status']=='PUBLICATION_HUMAN_GATE_RETENTION_WATCH_DRIFT'; assert 'selected_option_null' in r['drift']
