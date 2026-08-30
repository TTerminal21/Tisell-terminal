"""Start the whole terminal with one command.

    python run.py

Launches the FastAPI data layer and the Streamlit dashboard together, waits for
the API to answer before starting the UI, and shuts both down on Ctrl-C.
"""
from __future__ import annotations

import subprocess
import sys
import time
import webbrowser
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API_PORT, UI_PORT = 8000, 8501
API_URL = f"http://127.0.0.1:{API_PORT}"


def interpreter() -> str:
    """Prefer the project venv, so `python run.py` works from a bare shell."""
    for candidate in (ROOT / ".venv" / "bin" / "python",
                      ROOT / ".venv" / "Scripts" / "python.exe"):
        if candidate.exists():
            return str(candidate)
    return sys.executable


def _wait(url: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2):
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    return False


def wait_for_api(timeout: float = 60.0) -> bool:
    return _wait(f"{API_URL}/health", timeout)


def wait_for_ui(timeout: float = 90.0) -> bool:
    return _wait(f"http://127.0.0.1:{UI_PORT}/_stcore/health", timeout)


def main() -> int:
    python = interpreter()
    processes: list[subprocess.Popen] = []

    print(f"starting data layer on {API_URL} …")
    processes.append(subprocess.Popen(
        [python, "-m", "uvicorn", "--app-dir", "data-layer", "main:app",
         "--port", str(API_PORT), "--host", "127.0.0.1"],
        cwd=ROOT,
    ))

    if not wait_for_api():
        print("data layer did not come up; stopping.")
        for process in processes:
            process.terminate()
        return 1
    print("data layer up.")

    print(f"starting dashboard on http://localhost:{UI_PORT} …")
    # headless=true skips Streamlit's first-run email prompt, which otherwise
    # blocks on stdin and kills the launcher. We open the browser ourselves.
    processes.append(subprocess.Popen(
        [python, "-m", "streamlit", "run", "dashboard/app.py",
         "--server.port", str(UI_PORT), "--server.headless", "true",
         "--browser.gatherUsageStats", "false"],
        cwd=ROOT,
    ))

    if wait_for_ui():
        webbrowser.open(f"http://localhost:{UI_PORT}")

    print("\nboth running. Ctrl-C to stop.\n")
    try:
        while True:
            for process in processes:
                if process.poll() is not None:
                    print("a process exited; shutting down.")
                    raise KeyboardInterrupt
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        for process in reversed(processes):
            process.terminate()
        for process in reversed(processes):
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
