import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import get_settings
from app.main import app


def test_health(monkeypatch):
    monkeypatch.delenv("BAIDU_API_KEY", raising=False)
    monkeypatch.delenv("BAIDU_SECRET_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        result = client.get("/api/health").json()
    assert result == {"status": "ok", "ocr_configured": False}


def test_startup_rejects_an_empty_access_passcode(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSCODE", "")
    get_settings.cache_clear()

    with pytest.raises(ValidationError), TestClient(app):
        pass
