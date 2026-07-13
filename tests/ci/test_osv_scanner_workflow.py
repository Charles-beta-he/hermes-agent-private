from pathlib import Path

import yaml


def test_osv_scan_does_not_require_code_scanning_feature():
    workflow_path = Path(__file__).parents[2] / ".github" / "workflows" / "osv-scanner.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    scan_job = workflow["jobs"]["scan"]
    assert scan_job["with"]["upload-sarif"] is False
    assert scan_job["with"]["fail-on-vuln"] is False