"""Run inside a notebook image with a read-only root and no network access."""

import importlib.util
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def main():
    assert importlib.util.find_spec("playwright") is None
    assert importlib.util.find_spec("nbconvert") is None
    assert not Path("/opt/playwright").exists()
    Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
    notebook = Path("/work/export_check.py")
    notebook.write_text('''import marimo
app = marimo.App()

@app.cell
def _():
    import marimo as mo
    result = 6 * 7
    mo.md(f"Export check: {result}")
    return (result,)

if __name__ == "__main__":
    app.run()
''')
    marimo = "/app/.venv/bin/marimo"
    for format in ("html", "ipynb"):
        output = Path(f"/tmp/export_check.{format}")
        subprocess.run([marimo, "export", format, str(notebook), "-o", str(output)],
                       check=True, timeout=60)
        assert output.stat().st_size > 0
        if format == "ipynb":
            assert json.loads(output.read_text())["nbformat"] == 4
    token = secrets.token_urlsafe(24)
    env = {**os.environ, "MARIMO_GATEWAY_TOKEN": token, "MARIMO_BASE_URL": "/workspaces/export-check"}
    with Path("/tmp/marimo-start.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "user_portal.notebook.start"],
                                   env=env, stdout=log, stderr=subprocess.STDOUT)
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError("Notebook startup failed: " + log.read())
                try:
                    request = Request("http://127.0.0.1:2718/workspaces/export-check/",
                                      headers={"Authorization": f"Bearer {token}"})
                    with urlopen(request, timeout=1) as response:
                        assert response.status == 200
                    break
                except URLError:
                    time.sleep(0.25)
            else:
                raise AssertionError("Notebook startup timed out")
        finally:
            process.terminate()
            process.wait(timeout=15)
    print("PASS: notebook starts read-only and offline; HTML/Jupyter exports work; no browser dependencies")


if __name__ == "__main__":
    main()
