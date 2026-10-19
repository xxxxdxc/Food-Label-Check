import pytest
import tempfile

from app.config import SRC_ROOT, get_settings


def pytest_configure():
    root = SRC_ROOT / "data" / "test-runtime"
    root.mkdir(parents=True, exist_ok=True)
    tempfile.tempdir = str(root)


@pytest.fixture(autouse=True)
def configured_app(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_ONLY", "0")
    monkeypatch.setenv("ACCESS_PASSCODE", "test-passcode")
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret")
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("OCR_MODE", "cache")
    monkeypatch.setenv("VLM_ENABLED", "0")
    monkeypatch.setenv("VLM_MODEL", "test-model")
    monkeypatch.setenv("VLM_API_KEY", "test-key")
    monkeypatch.setenv("VLM_BASE_URL", "https://api.example.test/v1")
    monkeypatch.setenv("BAIDU_API_KEY", "offline-test-key")
    monkeypatch.setenv("BAIDU_SECRET_KEY", "offline-test-secret")
    from app.services.ocr_client import ServiceError
    def offline_vision(*_args):
        raise ServiceError("VLM_FALLBACK", "测试环境未调用外部模型")
    monkeypatch.setattr("app.services.vision_client.propose_tables", offline_vision)
    monkeypatch.setattr("app.services.regional_ocr.propose_regions", offline_vision)
    from app.deps import _buckets
    _buckets.clear()
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
