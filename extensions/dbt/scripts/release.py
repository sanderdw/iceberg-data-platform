"""Release checks and the source archive of the dbt extension, independent of the platform's release.

  uv run python -m scripts.release              # check versions, files and secrets
  uv run python -m scripts.release --tag dbt-v0.1.0 --build
  uv run python -m scripts.release --channel refs/heads/main   # release names for a Git ref (in Actions)
"""

import argparse
import gzip
import io
import json
import os
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".js", ".css", ".html", ".svg", ".md", ".yml", ".yaml", ".sql", ".csv", ".toml", ".lock",
            ".txt", ".ttf", ".example", ".json", ".tpl"}
PLAIN = {".helmignore", "Dockerfile", ".gitignore", ".dockerignore", ".python-version", "LICENSE", "NOTICE"}
BINARY = {".ttf"}
SECRET = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|BRIDGE_CLIENT_SECRET=[0-9a-f]{16,}")


def tracked(root=ROOT):
    output = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "."]).decode()
    return sorted(Path(p) for p in output.split("\0") if p)


def check(root=ROOT, tag=None):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if f'VERSION = "{version}"' not in (root / "dbt_portal" / "config.py").read_text():
        raise SystemExit("dbt_portal/config.py VERSION differs from pyproject.toml")
    if f"## {version}" not in (root / "CHANGELOG.md").read_text():
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    chart = (root / "deploy/helm/iceberg-dbt/Chart.yaml").read_text()
    if not all(re.search(rf'^{key}: "?{re.escape(version)}"?$', chart, re.MULTILINE)
               for key in ("version", "appVersion")):
        raise SystemExit("deploy/helm/iceberg-dbt/Chart.yaml has another version")
    for compose in ("compose.yaml",):
        text = (root / compose).read_text()
        if f"iceberg-dbt:{version}" not in text or f"iceberg-dbt-runner:{version}" not in text:
            raise SystemExit(f"{compose} does not use the {version} images")
    contract = json.loads((root / "contracts" / "dbt-api" / "v1" / "openapi.yaml").read_text())
    if contract["info"]["version"] != version:
        raise SystemExit("contracts/dbt-api/v1/openapi.yaml has another version; run scripts.api_contract")
    if tag is not None and tag != f"dbt-v{version}":
        raise SystemExit(f"Tag {tag} does not match version dbt-v{version}")
    files = []
    for path in tracked(root):
        full = root / path
        if path.name.startswith(".env") and path.name != ".env.example":
            raise SystemExit(f"An environment file is tracked: {path}")
        if path.suffix not in SUFFIXES and path.name not in PLAIN:
            raise SystemExit(f"Unexpected file type: {path}")
        if path.suffix not in BINARY and SECRET.search(full.read_text(errors="replace")):
            raise SystemExit(f"Possible secret in {path}")
        files.append(path)
    return version, files


def build(root, version, files):
    name = f"iceberg-dbt-{version}"
    output = root / "dist" / f"{name}.tar.gz"
    output.parent.mkdir(exist_ok=True)
    with output.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, \
            tarfile.open(fileobj=zipped, mode="w") as tar:
        for path in files:
            data = (root / path).read_bytes()
            info = tarfile.TarInfo(f"{name}/{path.as_posix()}")
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    return output


def channel(ref, version, run_id, attempt):
    """A dbt-vX.Y.Z tag is a release; a branch is a preview with its own image and release tags."""
    if ref == f"refs/tags/dbt-v{version}":
        return {"version": version, "preview": "false", "image_tag": version, "release_tag": f"dbt-v{version}",
                "slug": "", "chart_version": version}
    if not ref.startswith("refs/heads/"):
        raise SystemExit(f"Run this workflow on a branch or on tag dbt-v{version}, not {ref}")
    # The same names as the platform's branch previews, so one branch's images line up.
    slug = re.sub(r"[^a-z0-9._-]", "-", ref.removeprefix("refs/heads/").lower())[:60].strip("-.")
    if not slug or re.match(r"v\d", slug):
        raise SystemExit("This branch name cannot be used for a preview")
    build = f"{slug}-{run_id}-{attempt}"
    # A chart version is SemVer: its prerelease part allows letters, digits and hyphens only.
    return {"version": version, "preview": "true", "image_tag": build, "release_tag": f"dbt-{build}", "slug": slug,
            "chart_version": f"{version}-{re.sub(r'[._]', '-', build)}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--channel", metavar="REF", help="Only print the release names for a Git ref")
    args = parser.parse_args()
    if args.channel:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        names = channel(args.channel, version, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        print("\n".join(f"{key}={value}" for key, value in names.items()))
        return
    version, files = check(tag=args.tag)
    print(f"PASS: iceberg-dbt {version}, {len(files)} files")
    if args.build:
        print(build(ROOT, version, files))


if __name__ == "__main__":
    main()
