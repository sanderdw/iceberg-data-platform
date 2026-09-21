"""Initialize persistent notebook once; secrets only live in the runtime environment."""

import os
import tomllib
from pathlib import Path

from user_portal.notebook.seed import seed_workspace

work = Path("/work")
seed_workspace(work)
# Keep editor preferences alongside the shared notebooks across restarts.
config = work / ".marimo.toml"
if not config.exists():
    config.write_text(
        '[display]\ntheme = "dark"\n\n[save]\nautosave = "after_delay"\nautosave_delay = 1000\n'
    )
# Use the image's uv installer, with packages directed to writable runtime storage.
if "package_management" not in tomllib.loads(config.read_text()):
    with config.open("a") as preferences:
        preferences.write('\n[package_management]\nmanager = "uv"\n')
Path(os.environ["HOME"]).mkdir(parents=True, exist_ok=True)
token = Path("/tmp/marimo-token")
token.write_text(os.environ.pop("MARIMO_GATEWAY_TOKEN"))
token.chmod(0o600)
os.execv(
    "/app/.venv/bin/marimo",
    [
        "marimo",
        "edit",
        str(work),
        "--host",
        "0.0.0.0",
        "--port",
        "2718",
        "--headless",
        "--no-sandbox",
        "--skip-update-check",
        "--token-password-file",
        str(token),
        "--base-url",
        os.environ["MARIMO_BASE_URL"],
    ],
)
