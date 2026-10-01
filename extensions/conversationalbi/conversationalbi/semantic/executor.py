"""Run compiled queries in disposable worker processes, a few at a time, with a hard time limit."""

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from ..errors import CbiError

ROOT = Path(__file__).resolve().parents[2]
MAX_OUTPUT = 16_000_000


class Executor:
    def __init__(self, *, timeout=30, concurrency=4, module="conversationalbi.semantic.worker"):
        self.timeout = timeout
        self.module = module
        self.slots = asyncio.Semaphore(concurrency)

    async def run(self, job):
        async with self.slots:
            with tempfile.TemporaryDirectory(prefix="cbi-query-") as home:
                process = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", self.module, cwd=home,
                    stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                    # Never inherit the gateway's secrets: the LLM key, the client secret, session keys.
                    env={"PATH": os.defpath, "HOME": home, "PYTHONPATH": str(ROOT), "PYTHONDONTWRITEBYTECODE": "1",
                         "DUCKDB_EXTENSION_DIRECTORY": os.environ.get(
                             "DUCKDB_EXTENSION_DIRECTORY", str(Path.home() / ".duckdb" / "extensions"))},
                )
                try:
                    stdout, _ = await asyncio.wait_for(process.communicate(json.dumps(job).encode()), self.timeout)
                except TimeoutError:
                    process.kill()
                    await process.wait()
                    raise CbiError(504, f"The query ran longer than {self.timeout} seconds. Narrow it with filters "
                                        "or fewer dimensions.", "query_timeout") from None
        if len(stdout) > MAX_OUTPUT:
            raise CbiError(413, "The result is too large. Ask for fewer rows or dimensions.", "result_too_large")
        try:
            result = json.loads(stdout or b"{}")
        except ValueError:
            result = {"error": "output"}
        if process.returncode or "error" in result:
            if result.get("status") in (401, 403):
                raise CbiError(403, "The platform refused to read a table of this model. It may no longer be "
                                    "shared with your team.", "table_not_readable")
            if result.get("status") == 404:
                raise CbiError(404, "A table of this model no longer exists.", "table_missing")
            raise CbiError(502, "The query could not run. Check the model's tables and try again.", "query_failed",
                           {"reason": result.get("error", "unknown")})
        return result
