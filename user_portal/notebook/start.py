"""Initialize persistent notebook once; secrets only live in the runtime environment."""

import os
import re
import tomllib
from pathlib import Path

from user_portal.notebook.seed import seed_workspace

# marimo ignores an empty dotenv list, so point it at a file that is always empty.
NO_DOTENV = 'dotenv = ["/dev/null"]'


def write_preferences(config):
    """Keep editor preferences alongside the shared notebooks across restarts."""
    if not config.exists():
        config.write_text(
            '[display]\ntheme = "dark"\n\n[save]\nautosave = "after_delay"\nautosave_delay = 1000\n'
        )
    original = text = config.read_text()
    preferences = tomllib.loads(text)
    # Use the image's uv installer, with packages directed to writable runtime storage.
    if "package_management" not in preferences:
        text += '\n[package_management]\nmanager = "uv"\n'
    # marimo loads the .env next to the notebooks by default. The whole team shares /work,
    # so one member's .env must not set variables in every member's kernel.
    runtime = preferences.get("runtime", {})
    if runtime.get("dotenv") == []:
        text = re.sub(r"^dotenv[ \t]*=[ \t]*\[\s*\]", NO_DOTENV, text, count=1, flags=re.MULTILINE)
    elif "dotenv" not in runtime:
        text, found = re.subn(
            r"^\[runtime\][ \t]*(#.*)?$", lambda header: header[0] + "\n" + NO_DOTENV, text, count=1,
            flags=re.MULTILINE,
        )
        if not found:
            text += "\n[runtime]\n" + NO_DOTENV + "\n"
    if text != original:
        config.write_text(text)


def main():
    work = Path("/work")
    seed_workspace(work)
    write_preferences(work / ".marimo.toml")
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


if __name__ == "__main__":
    main()
