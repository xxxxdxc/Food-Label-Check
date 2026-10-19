import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from app.config import get_settings
from app.db import CheckRun, Document, Task, init_db
from app.engine_bridge import check
from app.main import app
from app.services.samples import bind_document
from app.services.vision_client import propose_tables
from app.worker import run_one
from tests.helpers import image_bytes, synthetic_document
from tests.test_boundaries import structural_doc, proposals
from tests.test_workflow import client, create_upload, extraction


def test_ready_extraction_enqueues_separate_check_job(client, monkeypatch):
    extraction(monkeypatch, False)
    task = create_upload(client)
    assert run_one()
    assert client.get("/api/audits/" + task["id"]).json()["status"] == "CHECKING"
    assert run_one()
    assert client.get("/api/audits/" + task["id"]).json()["status"] == "PARTIAL"


def test_per_100ml_does_not_suppress_independent_packaging_check():
    doc = synthetic_document(False)
    doc["facts"]["nutritionTables"][0]["basis"].update(kind="per_100ml", raw="每100mL")
    _, output, _ = check(doc, "0")
    assert any(i["rule_id"] == "PACKAGE-FIELD-FOOD_NAME" and i["status"] == "PASS" for i in output["findings"])
    assert any(i["rule_id"] == "OCR-DOWNSTREAM-GATE" and i["status"] == "NOT_CHECKED" for i in output["findings"])
    assert not any(i["rule_id"].startswith("NRV-") and i["status"] == "PASS" for i in output["findings"])


def test_print_report_escapes_untrusted_ocr_and_has_no_external_resources(client, monkeypatch):
    doc = synthetic_document()
    doc["facts"]["productName"].update(raw="<script>alert(1)</script>", value="<script>alert(1)</script>")
    monkeypatch.setattr("app.worker.extract", lambda task, _: bind_document(doc, task.id, task.files[0]))
    task = create_upload(client)
    assert run_one()
    html = client.get("/api/audits/" + task["id"] + "/report?format=html")
    assert "<script>" not in html.text
    assert "default-src 'none'" in html.headers["content-security-policy"]


def test_spa_fallback_does_not_expose_data_or_turn_missing_api_into_html(client, monkeypatch, tmp_path):
    root = tmp_path / "dist"
    root.mkdir()
    (root / "index.html").write_text("<div>frontend</div>")
    (root / "app.js").write_text("console.log('frontend')")
    monkeypatch.setenv("FRONTEND_DIST", str(root))
    get_settings.cache_clear()
    assert "frontend" in client.get("/audits/task/review").text
    assert client.get("/app.js").status_code == 200
    assert client.get("/api/unknown").status_code == 404
    assert client.get("/data/app.db").status_code == 404


def test_cookie_works_with_unicode_passcode_and_secure_https(monkeypatch):
    monkeypatch.setenv("ACCESS_PASSCODE", "比赛口令")
    get_settings.cache_clear()
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/login", json={"passcode": "比赛口令"})
        assert response.status_code == 200
        assert "Secure" in response.headers["set-cookie"] and "HttpOnly" in response.headers["set-cookie"]
        assert client.get("/api/audits").status_code == 200


def test_actual_sdk_payload_is_tool_free_scaled_and_uses_strict_import(monkeypatch):
    from PIL import Image
    import io
    monkeypatch.setenv("VLM_ENABLED", "1")
    monkeypatch.setenv("VLM_API_KEY", "fake-test-key")
    monkeypatch.setenv("VLM_MODEL", "fake-model")
    get_settings.cache_clear()
    captured = {}
    doc = structural_doc()
    doc["images"][0].update(widthPx=4000, heightPx=3000)
    proposal = proposals()
    proposal["tables"][0]["boxPx"] = {"left": 10, "top": 20, "width": 100, "height": 80}

    def respond(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"id": "test", "object": "chat.completion", "created": 1, "model": "fake-model",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": json.dumps(proposal)}, "finish_reason": "stop"}]})
    factory = httpx.Client
    class MockClient(factory):
        def __init__(self, **kwargs):
            super().__init__(transport=httpx.MockTransport(respond), **kwargs)
    monkeypatch.setattr(httpx, "Client", MockClient)
    stream = io.BytesIO()
    Image.new("RGB", (4000, 3000), "white").save(stream, format="PNG")
    updated = propose_tables(doc, stream.getvalue())
    assert "tools" not in captured
    import base64
    content = captured["messages"][0]["content"]
    with Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"]["url"].split(",")[1]))) as image:
        assert image.size == (2000, 1500)
    assert updated["tableDiagnostics"][0]["boxPx"] == {"left": 20, "top": 40, "width": 200, "height": 160}


def test_chunked_body_limit_is_413(client):
    def body():
        for _ in range(3):
            yield b"x" * (1024*1024)
    response = client.post("/api/auth/login", content=body(), headers={"content-type": "application/json"})
    assert response.status_code == 413, response.text


def test_uploaded_png_applies_exif_orientation_before_stripping_metadata():
    import io
    from PIL import Image
    from app.services.storage import validate_image
    image = Image.new("RGB", (120, 100), "white")
    exif = Image.Exif()
    exif[274] = 6
    source = io.BytesIO()
    image.save(source, format="PNG", exif=exif)
    encoded, width, height, *_ = validate_image(source.getvalue())
    with Image.open(io.BytesIO(encoded)) as sanitized:
        assert not sanitized.getexif()
        assert sanitized.size == (width, height) == (100, 120)
