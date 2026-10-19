"""Run the API and independent worker under one container lifecycle."""
import signal
import subprocess
import sys
import time

from app.config import get_settings
from app.db import init_db


def main():
    get_settings()
    init_db()
    children = []
    stopping = False

    def stop(_signal, _frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for arguments in (("uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1", "--no-proxy-headers"),
                          ("app.worker",)):
            children.append(subprocess.Popen([sys.executable, "-m", *arguments]))
        while not stopping:
            if any(child.poll() is not None for child in children):
                raise RuntimeError("An application process exited; container must restart")
            time.sleep(.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()


if __name__ == "__main__":
    main()
