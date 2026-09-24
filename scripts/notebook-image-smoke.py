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


def check_native_threads():
    # Exercise real native pools, not just environment settings. Multiple notebooks
    # share one runtime PID budget; host-sized pools previously exhausted it.
    source = """
import json
from pathlib import Path
import numpy as np
import pyarrow as pa
import duckdb
from user_portal.notebook.flights import generate_flights, load_semantics, quality_report, require_quality
np.ones((32, 32)) @ np.ones((32, 32))
pa.table({'value': [1, 2, 3]}).to_pandas()
model, contract, _ = load_semantics('/app/user_portal/notebook/examples')
with duckdb.connect(config={'threads': 2, 'memory_limit': '128MB'}) as connection:
    generate_flights(connection, model)
    require_quality(quality_report(connection, contract))
    assert connection.execute('SELECT count(*) FROM FLIGHT').fetchone() == (12000,)
    threads = int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines()
                       if line.startswith('Threads:')))
    assert threads <= 16, f'Native pools use {threads} threads per kernel'
print(json.dumps({'threads': threads}))
"""
    processes = []
    try:
        for _ in range(4):
            processes.append(subprocess.Popen(
                [sys.executable, '-c', source], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            ))
        for process in processes:
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, f'Flights kernel exited {process.returncode}: {stderr}'
            assert json.loads(stdout)['threads'] <= 16
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.wait()
    print('PASS: four concurrent flights kernels generate 12,000 rows with bounded native thread pools')


def main():
    check_native_threads()
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
    # A teammate's .env in the shared filespace must not reach other members' kernels.
    dotenv = subprocess.run(
        [sys.executable, "-c", ("from marimo._config.manager import get_default_config_manager as m; "
                                "print(m(current_path='/work').get_config(hide_secrets=False)['runtime']['dotenv'])")],
        cwd="/work", check=True, capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    assert dotenv == "['/dev/null']", f"marimo loads {dotenv}"
    print("PASS: notebook starts read-only and offline; HTML/Jupyter exports work; no browser dependencies; no shared .env")


if __name__ == "__main__":
    main()
