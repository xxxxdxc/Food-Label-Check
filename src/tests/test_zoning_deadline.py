import asyncio
import io
import json
import time

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from app.config import get_settings
from app.services.ocr_client import ServiceError
from app.services.zoning import propose_regions
from tests.test_regional_ocr import layout


def image_bytes():
    stream=io.BytesIO()
    Image.new("RGB", (400,500), "white").save(stream,format="PNG")
    return stream.getvalue()


def mock_transport(monkeypatch, handler):
    factory=httpx.AsyncClient
    class Client(factory):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx,"AsyncClient",Client)
    monkeypatch.setenv("VLM_ENABLED","1")
    get_settings.cache_clear()


def response():
    return httpx.Response(200,json={"id":"test","object":"chat.completion","created":1,"model":"test", "choices":[{"index":0,"message":{"role":"assistant","content":json.dumps(layout())},"finish_reason":"stop"}]})


def test_zoning_has_independent_five_minute_default_and_phase_timeouts(monkeypatch):
    monkeypatch.setenv("VLM_TIMEOUT_S","60")
    calls=[]
    def handler(request):
        calls.append(request)
        assert 120 < request.extensions["timeout"]["read"] <= 300
        return response()
    mock_transport(monkeypatch,handler)
    settings=get_settings()
    assert settings.zoning_timeout_s == 300 and settings.vlm_timeout_s == 60
    assert propose_regions(image_bytes()) == layout()
    assert len(calls)==1
    monkeypatch.setenv("ZONING_TIMEOUT_S","301")
    get_settings.cache_clear()
    with pytest.raises(ValidationError):get_settings()


def test_waiting_for_headers_is_cancelled_at_deadline_without_retry(monkeypatch):
    monkeypatch.setenv("ZONING_TIMEOUT_S","0.4")
    calls=[]
    cancelled=[]
    async def handler(request):
        calls.append(request)
        try:await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        return response()
    mock_transport(monkeypatch,handler)
    started=time.monotonic()
    with pytest.raises(ServiceError) as caught:propose_regions(image_bytes())
    assert caught.value.code=="ZONING_TIMEOUT"
    assert "0.4 秒" in caught.value.message
    assert len(calls)==1 and cancelled==[True]
    assert time.monotonic()-started < 2


def test_periodic_response_chunks_cannot_extend_total_deadline(monkeypatch):
    monkeypatch.setenv("ZONING_TIMEOUT_S","0.4")
    calls=[]
    chunks=[]
    closed=[]
    class Body(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(100):
                await asyncio.sleep(.05)
                chunks.append(1)
                yield b" "
        async def aclose(self):closed.append(True)
    def handler(request):
        calls.append(request)
        return httpx.Response(200,stream=Body())
    mock_transport(monkeypatch,handler)
    started=time.monotonic()
    with pytest.raises(ServiceError) as caught:propose_regions(image_bytes())
    assert caught.value.code=="ZONING_TIMEOUT"
    assert len(calls)==1 and chunks and len(chunks)<100 and closed
    assert time.monotonic()-started < 2
