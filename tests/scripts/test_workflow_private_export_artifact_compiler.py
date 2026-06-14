from __future__ import annotations
import importlib.util, json
from pathlib import Path
SCRIPT=Path(__file__).resolve().parents[2]/'scripts'/'workflow_private_export_artifact_compiler.py'
def load():
    spec=importlib.util.spec_from_file_location('m',SCRIPT); assert spec and spec.loader
    m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
def write(root,rel,payload):
    p=root/rel; p.parent.mkdir(parents=True,exist_ok=True); p.write_text(json.dumps(payload))
def seed(root, apply=False):
    write(root,'docs/private/workflow-kernel-publication-apply-authorization-packet/workflow_kernel_publication_apply_authorization_packet_20260613_phase74.json',{'status':'PUBLICATION_APPLY_AUTHORIZATION_PACKET_READY','publication_apply_enabled':apply,'packet_digest':'abc'})
    write(root,'docs/private/workflow-kernel-manual-only-governance-archive/workflow_kernel_manual_only_governance_archive_20260613_phase68.json',{'status':'MANUAL_ONLY_GOVERNANCE_ARCHIVE_READY'})
    write(root,'docs/private/workflow-kernel-sequence-map/workflow_kernel_sequence_map_20260613_phase38.json',{'status':'WORKFLOW_SEQUENCE_MAP_READY'})
def test_private_export_ready(tmp_path):
    m=load(); seed(tmp_path); r=m.build_export(tmp_path)
    assert r['status']=='PRIVATE_EXPORT_ARTIFACT_COMPILER_READY'
    assert r['export_manifest']['visibility']=='private_local_only'
    assert r['publication_apply_enabled'] is False
    assert r['external_publication_enabled'] is False
def test_private_export_blocks_if_apply_enabled(tmp_path):
    m=load(); seed(tmp_path, True); r=m.build_export(tmp_path)
    assert r['status']=='PRIVATE_EXPORT_ARTIFACT_COMPILER_BLOCKED'
    assert 'authorization_apply_disabled' in r['blockers']
