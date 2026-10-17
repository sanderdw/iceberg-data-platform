"""A run on Kubernetes: fetch the staged /work from the launcher, run dbt, return out/.

The launcher hands the run RUN_URL, RUN_ID and a one-run RUN_TICKET instead of copying files in and
out of the container (launcher/kubernetes.py). Standard library only; the ticket never reaches the
pipeline's code.
"""

import io
import os
import sys
import tarfile
import urllib.request
from pathlib import Path

from runner import entrypoint, guard

WORK = Path(os.environ.get("RUNNER_WORK", "/work"))
# Read once and removed from the environment, so the project's code never sees it.
TICKET = os.environ.pop("RUN_TICKET", "")


def call(method, path, data=None):
    request = urllib.request.Request(f"{os.environ['RUN_URL']}/jobs/{os.environ['RUN_ID']}/{path}", data=data,
                                     method=method, headers={"Authorization": f"Bearer {TICKET}"})
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.read()


def fetch():
    with tarfile.open(fileobj=io.BytesIO(call("GET", "input"))) as tar:
        tar.extractall(WORK, filter="data")


def upload():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        tar.add(WORK / "out", arcname="out")
    call("PUT", "output", buffer.getvalue())


def main():
    fetch()
    try:
        code = entrypoint.main()
    finally:
        # The source guard refuses private addresses, the launcher's included.
        guard.uninstall()
        upload()
    return code


if __name__ == "__main__":
    sys.exit(main())
