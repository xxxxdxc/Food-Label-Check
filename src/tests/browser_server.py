"""Isolated browser-test server. Synthetic extraction ONLY; never run for users.

Real upload validation, SQLite queue, rules, corrections and reports are retained.
Each invocation uses a new data directory and refuses external recognition calls.
"""
import os
from pathlib import Path
import tempfile
from contextlib import asynccontextmanager
from threading import Event, Thread


root = Path(__file__).resolve().parents[1] / "data" / "test-runtime"
root.mkdir(parents=True, exist_ok=True)
os.environ.update(
    LOCAL_ONLY="1", OCR_MODE="live", VLM_ENABLED="1",
    BAIDU_API_KEY="offline-test", BAIDU_SECRET_KEY="offline-test",
    VLM_API_KEY="offline-test", VLM_MODEL="offline-test",
    VLM_BASE_URL="https://offline.invalid/v1",
    DATA_DIR=tempfile.mkdtemp(prefix="browser-", dir=root),
    DAILY_OCR_LIMIT="100", DAILY_VLM_LIMIT="0",
)

from app.main import app
from app import worker
from app.services.samples import bind_document
from tests.helpers import synthetic_document


def forbidden_external_call(*args, **kwargs):
    raise AssertionError("External OCR / VLM is forbidden in offline browser tests")


worker.BaiduClient.recognize = forbidden_external_call
import app.services.vision_client as vision
vision.propose_tables = forbidden_external_call
import app.services.regional_ocr as regional
regional.propose_regions = forbidden_external_call


def synthetic_extraction(task, _):
    doc = synthetic_document(not task.files[0].display_name.startswith("auto-"))
    doc["_platform"] = {"pipeline": "vlm_zones_ocr_v1", "extraction": []}
    return bind_document(doc, task.id, task.files[0])


worker.extract = synthetic_extraction
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def test_lifespan(application):
    async with original_lifespan(application):
        stop = Event()

        def consume():
            while not stop.is_set():
                if not worker.run_one():
                    stop.wait(.1)

        thread = Thread(target=consume, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(timeout=5)


app.router.lifespan_context = test_lifespan


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5193, proxy_headers=False)
