from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def local_mode(monkeypatch):
    monkeypatch.setenv("LOCAL_ONLY", "1")
    monkeypatch.setenv("ACCESS_PASSCODE", "")
    monkeypatch.setenv("SESSION_SECRET", "")
    get_settings.cache_clear()


def test_local_workbench_needs_no_credentials(monkeypatch):
    local_mode(monkeypatch)
    monkeypatch.setenv("OCR_MODE", "live")
    get_settings.cache_clear()
    with TestClient(app, base_url="http://127.0.0.1:5182", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/audits").status_code == 200
        assert client.get("/api/standards").status_code == 200
        assert not client.cookies
        assert client.post("/api/auth/login", json={"passcode": "unused"}).status_code == 410
        assert client.post("/api/audits", data={"source": "upload"}, files={"files": ("bad.png", b"invalid", "image/png")}).status_code == 415
        schema = client.get("/openapi.json").json()
        assert "/api/auth/login" not in schema["paths"]
        assert not schema["paths"]["/api/audits"]["get"].get("security")


def test_local_mode_rejects_remote_clients_and_rebinding(monkeypatch):
    local_mode(monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1", client=("192.168.1.2", 50000)) as client:
        assert client.get("/api/audits").json()["error"]["code"] == "LOCAL_ONLY"
    with TestClient(app, base_url="http://untrusted.example", client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/audits").status_code == 403
        assert client.get("/").status_code == 403


def test_local_mode_keeps_cross_origin_write_guard(monkeypatch):
    local_mode(monkeypatch)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 50000)) as client:
        response = client.post("/api/audits", headers={"Origin": "https://untrusted.example"}, data={"source": "upload"})
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "ORIGIN_REJECTED"
