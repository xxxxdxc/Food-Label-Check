"""Exercise real HTTP and a separate Worker process without external OCR."""
import os
import secrets
import socket
import subprocess
import sys
import time

import httpx
from app.config import SRC_ROOT


def test_separate_api_worker_persist_and_restart(tmp_path):
    with socket.socket() as port_socket:
        port_socket.bind(("127.0.0.1", 0))
        port = port_socket.getsockname()[1]
    passcode = secrets.token_hex(16)
    env = {**os.environ, "ACCESS_PASSCODE": passcode, "SESSION_SECRET": secrets.token_hex(32),
           "DATA_DIR": str(tmp_path / "process-data"), "OCR_MODE": "cache", "VLM_ENABLED": "0"}
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    log_path = tmp_path / "api.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1",
            "--port", str(port), "--workers", "1"], cwd=SRC_ROOT, env=env, stdout=log, stderr=log, creationflags=flags)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5, trust_env=False) as client:
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    assert process.poll() is None, "API process exited before startup"
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.TransportError:
                        time.sleep(.1)
                else:
                    raise AssertionError("API startup timed out")
                assert client.get("/api/audits").status_code == 401
                assert client.post("/api/auth/login", json={"passcode": passcode}).status_code == 200
                response = client.post("/api/audits", data={"source": "sample", "sample_id": "4260290260010"})
                assert response.status_code == 201, response.text
                task = response.json()
                worker = subprocess.run([sys.executable, "-m", "app.worker", "--once"], cwd=SRC_ROOT,
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, creationflags=flags)
                assert worker.returncode == 0
                prefix = "/api/audits/" + task["id"]
                state = client.get(prefix).json()
                assert state["status"] in {"WAITING_REVIEW", "CHECKING", "PARTIAL"}, state
                if state["status"] == "CHECKING":
                    subprocess.run([sys.executable, "-m", "app.worker", "--once"], cwd=SRC_ROOT, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True, creationflags=flags)
                # A new worker invocation sees the persisted queue; it does not rerun completed jobs.
                subprocess.run([sys.executable, "-m", "app.worker", "--once"], cwd=SRC_ROOT, env=env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=True, creationflags=flags)
                assert client.get(prefix + "/report").status_code == 200
                assert client.get(task["files"][0]["url"]).status_code == 200
        finally:
            process.terminate()
            process.wait(timeout=10)
