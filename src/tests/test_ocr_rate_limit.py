import io
import json
from datetime import datetime, timezone

import pytest
from app.db import Usage, init_db
from app.services import ocr_client as ocr


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    sleeps = []
    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(ocr.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ocr.time, "sleep", sleep)
    monkeypatch.setattr(ocr, "_next_ocr_request_at", 0.0)
    monkeypatch.setattr(ocr.BaiduClient, "access_token", lambda self: "test-token")
    return now, sleeps


@pytest.mark.parametrize("provider_code,code,retryable", [(18,"OCR_RATE_LIMIT",True),(17,"OCR_QUOTA",False),(19,"OCR_QUOTA",False)])
def test_provider_limits_keep_the_actual_reason(monkeypatch, provider_code, code, retryable):
    monkeypatch.setattr(ocr, "urlopen", lambda *_a, **_k: io.BytesIO(json.dumps({"error_code":provider_code}).encode()))
    with pytest.raises(ocr.ServiceError) as caught:
        ocr.BaiduClient().post("https://example.test", {})
    assert caught.value.code == code
    assert caught.value.retryable is retryable
    assert str(provider_code) in caught.value.message


def test_fast_crops_and_new_clients_share_request_pacing(monkeypatch, clock):
    timestamps=[]
    def post(self, *_args):
        timestamps.append(clock[0][0])
        return {"words_result": []}
    monkeypatch.setattr(ocr.BaiduClient, "post", post)
    for _ in range(4):
        ocr.BaiduClient().recognize(b"image", "accurate")
    assert all(b-a >= 1.049 for a,b in zip(timestamps,timestamps[1:]))


def test_rate_limit_retries_only_failed_request_and_counts_attempts(monkeypatch, clock):
    replies=iter([{"error_code":18}, {"error_code":18}, {"words_result":[]}])
    monkeypatch.setattr(ocr, "urlopen", lambda *_a, **_k: io.BytesIO(json.dumps(next(replies)).encode()))
    assert ocr.BaiduClient().recognize(b"image", "accurate") == {"words_result":[]}
    assert 1 in clock[1] and 2 in clock[1]
    with init_db()() as db:
        day=datetime.now(timezone.utc).date().isoformat()
        assert db.get(Usage,(day,"ocr")).count == 3


@pytest.mark.parametrize("error,expected_attempts", [(18,4),(17,1),(19,1)])
def test_retry_is_bounded_and_does_not_retry_exhausted_quota(monkeypatch, clock, error, expected_attempts):
    calls=[]
    def response(*_a, **_k):
        calls.append(1)
        return io.BytesIO(json.dumps({"error_code":error}).encode())
    monkeypatch.setattr(ocr, "urlopen", response)
    with pytest.raises(ocr.ServiceError) as caught:
        ocr.BaiduClient().recognize(b"image", "accurate")
    assert len(calls) == expected_attempts
    assert caught.value.retryable is (error == 18)


def test_budget_error_reports_exact_shared_usage_and_raising_limit_preserves_it(monkeypatch):
    from app.config import get_settings
    from app.errors import AppError
    monkeypatch.setenv("DAILY_OCR_LIMIT", "3")
    get_settings.cache_clear()
    ocr.reserve_usage("ocr", 3)
    ocr.reserve_usage("ocr", 3)
    with init_db()() as db:
        with pytest.raises(AppError) as caught:
            ocr.require_ocr_capacity(db, 4)
        assert caught.value.code == "OCR_BUDGET"
        message = str(caught.value.detail)
        for text in ["上限为 3 次", "已用 2 次", "剩余 1 次", "需要 4 次", "08:00", "不代表百度"]:
            assert text in message
    monkeypatch.setenv("DAILY_OCR_LIMIT", "1000")
    get_settings.cache_clear()
    with init_db()() as db:
        ocr.require_ocr_capacity(db, 4)
        day = datetime.now(timezone.utc).date().isoformat()
        assert db.get(Usage, (day, "ocr")).count == 2


def test_exhausted_internal_budget_has_a_retryable_separate_code():
    ocr.reserve_usage("ocr", 1)
    with pytest.raises(ocr.ServiceError) as caught:
        ocr.reserve_usage("ocr", 1)
    assert caught.value.code == "OCR_BUDGET" and caught.value.retryable
    assert "已用 1 次" in caught.value.message and "剩余 0 次" in caught.value.message
