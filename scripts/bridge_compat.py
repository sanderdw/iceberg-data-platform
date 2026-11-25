"""Choose the platform release that an extension's compat job runs against.

That is the newest release tag that ships the Bridge, when its contract version is in the
extension's `bridge-contract` range (contracts/bridge/COMPATIBILITY.md). Otherwise the job skips its
steps and says why in the job summary. Standard library only, so a job runs it before any setup:

    python3 scripts/bridge_compat.py <extension>

It writes `tag` and `version` to `$GITHUB_OUTPUT` only when the release is in range.
"""

import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACT_FILE = "contracts/bridge/v1/openapi.yaml"


def version_tuple(value):
    return (*(int(part) for part in value.split(".")[:3]), 0, 0, 0)[:3]


def in_range(version, spec):
    """`version` against a comma-separated range such as `>=1.0,<2`."""
    current = version_tuple(version)
    checks = {">=": lambda b: current >= b, "<=": lambda b: current <= b, "==": lambda b: current == b,
              ">": lambda b: current > b, "<": lambda b: current < b}
    for clause in (c.strip() for c in spec.split(",") if c.strip()):
        op = next(op for op in (">=", "<=", "==", ">", "<") if clause.startswith(op))
        if not checks[op](version_tuple(clause[len(op):])):
            return False
    return True


def contract_version(bridge_source):
    """The contract version a release serves, from its server/bridge.py."""
    found = re.search(r'^CONTRACT_VERSION = "([0-9.]+)"', bridge_source, re.MULTILINE)
    return found.group(1) if found else None


def decide(extension, tags, show):
    """(tag, version, message): the release to test against, or no tag and the reason to skip.

    `tags` are the release tags, newest first; `show(tag, path)` returns a file of a tag, or None.
    """
    spec = tomllib.loads((ROOT / "extensions" / extension / "pyproject.toml").read_text())[
        "tool"][f"iceberg-{extension}"]["bridge-contract"]
    tag = next((t for t in tags if show(t, CONTRACT_FILE) is not None), None)
    if tag is None:
        return None, None, "No platform release ships the Bridge yet; the integration job covers this commit."
    version = contract_version(show(tag, "server/bridge.py") or "")
    if version is None or not in_range(version, spec):
        return None, version, (
            f"Skipped: the newest platform release, {tag}, offers Bridge contract {version or 'unknown'}, outside "
            f"this extension's range {spec}. The integration job covers this commit; this job runs again once a "
            "platform release in the range exists.")
    return tag, version, f"{tag} offers Bridge contract {version}, within {spec}."


def git_show(tag, path):
    result = subprocess.run(["git", "show", f"{tag}:{path}"], cwd=ROOT, capture_output=True, text=True, check=False)
    return result.stdout if result.returncode == 0 else None


def main():
    extension = sys.argv[1]
    tags = subprocess.run(["git", "tag", "--list", "v*", "--sort=-version:refname"], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout.split()
    tag, version, message = decide(extension, tags, git_show)
    print(message)
    if summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary, "a") as file:
            file.write(message + "\n")
    if tag and (output := os.environ.get("GITHUB_OUTPUT")):
        with open(output, "a") as file:
            file.write(f"tag={tag}\nversion={version}\n")


if __name__ == "__main__":
    main()
