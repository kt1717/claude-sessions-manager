import pytest
from fastapi.testclient import TestClient

from csm.server import create_app


@pytest.fixture
def client(mock_config):
    return TestClient(create_app(mock_config))


def test_sessions_endpoint(client):
    data = client.get("/api/sessions").json()
    ids = [s["id"] for s in data["sessions"]]
    assert "mock-aaa-active-known" in ids
    assert "mock-eee-unknown-everything" in ids


def test_session_detail_and_404(client):
    r = client.get("/api/sessions/mock-aaa-active-known")
    assert r.status_code == 200
    s = r.json()
    assert s["model"] == "claude-sonnet-5"
    assert s["usage"]["total_tokens"] == 206500
    assert client.get("/api/sessions/does-not-exist").status_code == 404


def test_tree_groups_missions(client):
    data = client.get("/api/tree").json()
    assert len(data["missions"]) >= 4
    roots = [m["project_root"] for m in data["missions"]]
    assert "/tmp/mock/dhcp-relay" in roots


def test_unknown_model_stays_null(client):
    s = client.get("/api/sessions/mock-bbb-active-unknown-model").json()
    assert s["model"] is None
    assert s["usage"]["total_tokens"] is None


def test_open_rejects_missing_dir(client):
    # mock paths don't exist on disk -> must refuse, not launch
    r = client.post("/api/sessions/mock-aaa-active-known/open")
    assert r.status_code == 400


def test_open_disabled_by_config(mock_config):
    mock_config.enable_gui_actions = False
    client = TestClient(create_app(mock_config))
    assert client.post("/api/sessions/mock-aaa-active-known/open").status_code == 403


def test_read_missing_file_404(client):
    r = client.get("/api/sessions/mock-aaa-active-known/read")
    assert r.status_code == 404


def test_read_real_file_redacted(mock_config, tmp_path, monkeypatch):
    # give one mock session a real markdown file containing a secret
    import json
    from tests.conftest import MOCK_FILE
    data = json.loads(MOCK_FILE.read_text())
    md = tmp_path / "note.md"
    md.write_text("# note\npassword=SuperSecret123\n")
    data["sessions"][0]["markdown_file"] = str(md)
    mock2 = tmp_path / "mock.json"
    mock2.write_text(json.dumps(data))
    mock_config.mock_data_file = str(mock2)
    client = TestClient(create_app(mock_config))
    r = client.get("/api/sessions/mock-aaa-active-known/read")
    assert r.status_code == 200
    assert "SuperSecret123" not in r.json()["content"]


def test_read_rejects_bad_which(client):
    r = client.get("/api/sessions/mock-aaa-active-known/read", params={"which": "../etc"})
    assert r.status_code == 400


def test_doctor_and_export(client):
    assert client.get("/api/doctor").status_code == 200
    exp = client.get("/api/export").json()
    assert "missions" in exp


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Claude Session Monitor" in r.text
