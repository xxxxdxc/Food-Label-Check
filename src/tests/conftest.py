import pytest

from app.config import get_settings


@pytest.fixture(autouse=True)
def configured_app(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSCODE", "test-passcode")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
