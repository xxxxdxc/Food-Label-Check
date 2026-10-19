import pytest
import tempfile

from app.config import SRC_ROOT, get_settings


def pytest_configure():
    root = SRC_ROOT / "data" / "test-runtime"
    root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(root)


@pytest.fixture(autouse=True)
def configured_app(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_PASSCODE", "test-passcode")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OCR_MODE", "cache")
    monkeypatch.setenv("VLM_ENABLED", "0")
    from app.deps import _buckets
    _buckets.clear()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
