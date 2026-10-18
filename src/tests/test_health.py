from fastapi.testclient import TestClient

from app.main import app


def test_health(monkeypatch):
    monkeypatch.delenv("BAIDU_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_SECRET_KEY", raising=False)
    with TestClient(app) as client:
        result = client.get("/api/health").json()
    assert result == {"status": "ok", "ocr_configured": False}
