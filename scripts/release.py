"""Validate public source files and build a reproducible, allowlisted source archive."""

import argparse
import gzip
import hashlib
import io
import json
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
ROOT_FILES = (
    ".dockerignore",
    ".editorconfig",
    ".env.example",
    ".gitattributes",
    ".gitignore",
    ".python-version",
    "CHANGELOG.md",
    "CONTRIBUTING.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "THIRD_PARTY_NOTICES.md",
    "Dockerfile",
    "compose.yaml",
    "compose.lan.yaml",
    "compose.users.yaml",
    "compose.users.lan.yaml",
    "package.json",
    "package-lock.json",
    "playwright.config.mjs",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_DIRS = ("server", "public", "user_portal", "scripts", "test", "docs", ".github")
SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".svg", ".ttf", ".txt", ".md", ".yaml", ".yml"}
IGNORED = {"__pycache__", ".pytest_cache", ".ruff_cache"}


def release_files(root=ROOT):
    files = [root / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if any(part in IGNORED for part in relative.parts):
                continue
            if path.is_symlink():
                raise ValueError(f"Symlinks are not allowed in public source: {relative}")
            if path.is_file():
                if path.name != "Dockerfile" and path.suffix not in SUFFIXES:
                    raise ValueError(f"Unexpected file in source directory: {relative}")
                files.append(path)
    for path in files:
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing or invalid release file: {path.relative_to(root)}")
    return sorted(set(files))


def check(root=ROOT):
    files = release_files(root)
    public_paths = {p.relative_to(root).as_posix() for p in files}
    if (root / ".git").exists():
        tracked = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z"]).decode().split("\0")
        excluded_tracked = sorted(set(filter(None, tracked)) - public_paths)
        if excluded_tracked:
            raise ValueError(f"Git tracks files outside the public release: {', '.join(excluded_tracked)}")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    package = json.loads((root / "package.json").read_text())
    package_lock = json.loads((root / "package-lock.json").read_text())
    if project["version"] != package["version"] or package["version"] != package_lock["version"]:
        raise ValueError("Release versions differ between Python, npm and the npm lockfile")
    if project.get("license") != "Apache-2.0" or package.get("license") != "Apache-2.0":
        raise ValueError("Expected Apache-2.0 project metadata")
    local_secrets = []
    env_file = root / ".env"
    if env_file.is_file():
        for line in env_file.read_text().splitlines():
            key, _, value = line.partition("=")
            if ("SECRET" in key or "PASSWORD" in key) and len(value.strip()) >= 12:
                local_secrets.append(value.strip().strip("\"'"))
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix == ".ttf":
            continue
        content = path.read_text()
        if any(secret in content for secret in local_secrets):
            raise ValueError(f"Local credential found in {relative}; value suppressed")
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
            raise ValueError(f"Private key found in {relative}")
        if re.search(r"/home/[a-zA-Z0-9_-]+/|192\.168\.\d{1,3}\.\d{1,3}", content):
            raise ValueError(f"Machine-specific path or LAN address found in {relative}")
        if path.suffix == ".md":
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", content):
                if "://" in target or target.startswith(("#", "mailto:")):
                    continue
                target = unquote(target.strip("<>").split("#")[0])
                linked = (path.parent / target).resolve()
                if not linked.is_relative_to(root.resolve()) or not linked.exists():
                    raise ValueError(f"Broken local documentation link in {relative}: {target}")
                if linked.is_file() and linked.relative_to(root).as_posix() not in public_paths:
                    raise ValueError(f"Documentation links to excluded file in {relative}: {target}")
    for name in ("doto", "space-grotesk", "space-mono"):
        if f"public/fonts/{name}-OFL.txt" not in public_paths:
            raise ValueError(f"Missing font license: {name}")
    print(f"PASS: {len(files)} public files; metadata, links, font notices and local-secret checks")
    return project["version"], files


def build(root=ROOT, output=None):
    version, files = check(root)
    output = output or root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    name = f"iceberg-data-platform-{version}"
    archive = output / f"{name}.tar.gz"
    with (
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as tar,
    ):
        for path in files:
            data = path.read_bytes()
            info = tarfile.TarInfo(f"{name}/{path.relative_to(root).as_posix()}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    (output / "SHA256SUMS").write_text(f"{digest}  {archive.name}\n")
    (output / "source-manifest.txt").write_text(
        "\n".join(p.relative_to(root).as_posix() for p in files) + "\n"
    )
    print(f"Built {archive.name} ({archive.stat().st_size:,} bytes)")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Also build dist/source archive and checksum")
    args = parser.parse_args()
    try:
        build() if args.build else check()
    except ValueError as exc:
        parser.exit(1, f"Release check failed: {exc}\n")


if __name__ == "__main__":
    main()
