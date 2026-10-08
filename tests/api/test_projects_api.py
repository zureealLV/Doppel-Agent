from fastapi.testclient import TestClient

from doppel_agent.api import create_app
from doppel_agent.provider import MockProvider


def test_current_project_uses_owned_workspace_and_never_returns_settings(tmp_path):
    (tmp_path / ".env").write_text("SECRET_NOT_IN_IDENTITY", encoding="utf-8")
    app = create_app(tmp_path, provider=MockProvider())
    with TestClient(app, base_url="http://127.0.0.1") as client:
        response = client.get("/api/v1/projects/current?path=C:/not-owned")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "name": tmp_path.name,
            "path": str(tmp_path.resolve()),
            "switching_available": False,
        }
        assert "SECRET_NOT_IN_IDENTITY" not in response.text
        assert client.post("/api/v1/projects/current", json={"path": "C:/not-owned"}).status_code == 405


def test_current_project_inherits_origin_and_host_checks(tmp_path):
    with TestClient(create_app(tmp_path, provider=MockProvider()), base_url="http://127.0.0.1") as client:
        assert client.get("/api/v1/projects/current", headers={"Origin": "https://evil.invalid"}).status_code == 403
        assert client.get("/api/v1/projects/current", headers={"Host": "evil.invalid"}).status_code == 403
