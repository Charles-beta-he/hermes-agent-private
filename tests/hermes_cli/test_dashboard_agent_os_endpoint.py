from fastapi.testclient import TestClient

from hermes_cli import web_server
from tests.hermes_cli.test_agent_os import _seed_packet


def test_dashboard_agent_os_status_endpoint_is_read_only_projection(tmp_path, monkeypatch):
    _seed_packet(tmp_path)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app, base_url="http://127.0.0.1:9119")

    response = client.get(
        "/api/agent-os/status",
        headers={"X-Hermes-Session-Token": web_server._SESSION_TOKEN},
    )
    web_server.app.state.auth_required = prev_required

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["capabilities"]["node_count"] == 3
    assert body["authority"]["external_side_effects_executed"] is False
    assert body["mission_runtime"]["runtime_mutation"] is False
    assert body["device_agent"]["device_physical_effect_executed"] is False
    assert body["side_effects_executed"] is False
    assert body["ux_shell"]["panels"] == ["Today", "Model Console"]
