import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def public_settings(monkeypatch):
    monkeypatch.setenv("PUBLIC_ACCESS", "1")
    monkeypatch.setenv("SESSION_SECRET", "isolated-public-visitor-secret-32-characters")
    monkeypatch.setenv("ALLOWED_ORIGINS", '["https://workbench.ms.show"]')
    get_settings.cache_clear()


def test_public_visitors_need_no_login_and_cannot_read_other_tasks(monkeypatch):
    public_settings(monkeypatch)
    with TestClient(app, base_url="https://workbench.ms.show") as first, TestClient(app, base_url="https://workbench.ms.show") as second:
        a = first.get("/api/audits")
        assert a.status_code == 200 and a.json()["total"] == 0
        assert "HttpOnly" in a.headers["set-cookie"] and "Secure" in a.headers["set-cookie"]
        assert "Partitioned" in a.headers["set-cookie"]
        created = first.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"}, headers={"Origin": "https://workbench.ms.show"})
        assert created.status_code == 201, created.text
        task = created.json()
        assert first.get("/api/audits").json()["total"] == 1
        assert second.get("/api/audits").json()["total"] == 0
        assert second.get("/api/audits/" + task["id"]).status_code == 404
        assert second.get(task["files"][0]["url"]).status_code == 404
        assert first.get(task["files"][0]["url"]).status_code == 200
        second.cookies.set("flc_visitor", "forged.invalid")
        assert second.get("/api/audits/" + task["id"]).status_code == 404


def test_proxy_host_only_accepts_exact_configured_public_origin(monkeypatch):
    public_settings(monkeypatch)
    with TestClient(app, base_url="https://proxy.internal") as client:
        headers = {"Origin": "https://workbench.ms.show"}
        assert client.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"}, headers=headers).status_code == 201
        for origin in ("https://evil.example", "https://workbench.ms.show.evil.example", "https://workbench.ms.show:444"):
            assert client.post("/api/audits", headers={"Origin": origin}).status_code == 403


def test_public_mode_requires_private_signing_secret(monkeypatch):
    monkeypatch.setenv("PUBLIC_ACCESS", "1")
    monkeypatch.setenv("SESSION_SECRET", "")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="SESSION_SECRET"):
        get_settings()
