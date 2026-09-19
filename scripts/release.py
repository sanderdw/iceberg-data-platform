"""Validate public source files and build a reproducible, allowlisted source archive."""

import argparse
import gzip
import hashlib
import io
import json
import os
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
    "install.sh",
    "install.ps1",
    "compose.yaml",
    "compose.lan.yaml",
    "compose.users.yaml",
    "compose.users.lan.yaml",
    "package.json",
    "package-lock.json",
    "pgadmin/servers.json",
    "playwright.config.mjs",
    "pyproject.toml",
    "uv.lock",
)
SOURCE_DIRS = ("server", "public", "user_portal", "scripts", "test", "docs", ".github")
BINARY_SUFFIXES = {".png", ".ttf"}
SUFFIXES = {".py", ".js", ".mjs", ".html", ".css", ".svg", ".txt", ".md", ".yaml", ".yml"} | BINARY_SUFFIXES
IGNORED = {"__pycache__", ".pytest_cache", ".ruff_cache"}
LOCAL_FILES = {"docs/conversation.md", "docs/dbaas-reference-architecture.drawio"}
# Tracked in git and published by the GitHub Pages workflow, but not part of the source release.
TRACKED_UNRELEASED = ("presentation/",)


def release_files(root=ROOT):
    files = [root / name for name in ROOT_FILES]
    for directory in SOURCE_DIRS:
        for path in (root / directory).rglob("*"):
            relative = path.relative_to(root)
            if relative.as_posix() in LOCAL_FILES or any(part in IGNORED for part in relative.parts):
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
        excluded_tracked = sorted(
            path for path in set(filter(None, tracked)) - public_paths if not path.startswith(TRACKED_UNRELEASED)
        )
        if excluded_tracked:
            raise ValueError(f"Git tracks files outside the public release: {', '.join(excluded_tracked)}")
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    package = json.loads((root / "package.json").read_text())
    package_lock = json.loads((root / "package-lock.json").read_text())
    if project["version"] != package["version"] or package["version"] != package_lock["version"]:
        raise ValueError("Release versions differ between Python, npm and the npm lockfile")
    if f'version="{project["version"]}"' not in (root / "server/app.py").read_text():
        raise ValueError("The API version in server/app.py differs from the project version")
    if project.get("license") != "Apache-2.0" or package.get("license") != "Apache-2.0":
        raise ValueError("Expected Apache-2.0 project metadata")
    local_secrets = []
    for env_file in root.glob(".env*"):
        if env_file.is_file() and env_file.name != ".env.example":
            for line in env_file.read_text().splitlines():
                key, _, value = line.partition("=")
                if ("SECRET" in key or "PASSWORD" in key) and len(value.strip()) >= 12:
                    local_secrets.append(value.strip().strip("\"'"))
    for path in files:
        relative = path.relative_to(root).as_posix()
        if path.suffix in BINARY_SUFFIXES:
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


def release_channel(ref, version, run_id, attempt):
    """Map a Git ref to its stable release or branch preview names."""
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("The project version must be X.Y.Z")
    if ref == f"refs/tags/v{version}":
        return {"version": version, "preview": "false", "release_tag": f"v{version}", "image_tag": version,
                "entrypoint": "", "slug": ""}
    if not ref.startswith("refs/heads/"):
        raise ValueError("Run this workflow on a branch or the vX.Y.Z tag matching the project version")
    # Branch names become release and image tags: keep them simple and out of the stable v* namespace.
    slug = re.sub(r"[^a-z0-9._-]", "-", ref.removeprefix("refs/heads/").lower())[:60].strip("-.")
    if not slug or re.match(r"v\d", slug):
        raise ValueError("This branch name cannot be used for a preview release")
    build = f"{slug}-{run_id}-{attempt}"
    return {"version": version, "preview": "true", "release_tag": build, "image_tag": build,
            "entrypoint": f"{slug}-preview", "slug": slug}


def installer_source(root, filename, release_tag="latest", image_tag="latest"):
    # Values become shell/PowerShell literals. Accept only simple release/image tags.
    for value in (release_tag, image_tag):
        if not re.fullmatch(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}", value):
            raise ValueError("Invalid release or image tag")
    if (release_tag == "latest") != (image_tag == "latest"):
        raise ValueError("A pinned installation needs both release and image tags")
    content = (root / filename).read_text()
    if filename == "install.sh":
        content = content.replace("    release_tag=latest\n", f"    release_tag={release_tag}\n")
    else:
        content = content.replace("    $ReleaseTag = 'latest'\n", f"    $ReleaseTag = '{release_tag}'\n")
    return content.replace("iceberg-data-platform-portal:latest", f"iceberg-data-platform-portal:{image_tag}").encode()


def build_install(root=ROOT, output=None, *, release_tag="latest", image_tag="latest"):
    """Render portable Compose files and matching stable or pinned installers."""
    version, _ = check(root)
    output = output or root / "dist"
    output.mkdir(parents=True, exist_ok=True)
    registry = "ghcr.io/sanderdw/iceberg-data-platform"
    image_names = {"portal": "portal", "monitor": "portal", "users": "users", "notebook-image": "notebook",
                   "keycloak-bootstrap": "portal"}
    contents = {name: installer_source(root, name, release_tag, image_tag) for name in ("install.sh", "install.ps1")}
    for filename in ("compose.yaml", "compose.users.yaml"):
        model = json.loads(subprocess.check_output([
            "docker", "compose", "--env-file", str(root / ".env.example"), "-f", str(root / filename),
            "config", "--no-interpolate", "--no-path-resolution", "--format", "json",
        ], cwd=root))
        for name, service in model["services"].items():
            # Compose can retain merged environments as KEY=value lists when
            # interpolation is disabled. Normalize without resolving secrets.
            if isinstance(service.get("environment"), list):
                service["environment"] = {
                    key: value if separator else None
                    for key, separator, value in (item.partition("=") for item in service["environment"])
                }
            service.pop("build", None)
            if name in image_names:
                service["image"] = f"{registry}-{image_names[name]}:{image_tag}"
            if name == "users":
                service["environment"]["NOTEBOOK_IMAGE"] = f"{registry}-notebook:{image_tag}"
        # JSON is valid YAML; keep Compose's normalized model without another dependency.
        contents[filename] = (json.dumps(model, indent=2) + "\n").encode()
    for filename in (".env.example", "scripts/setup.py", "pgadmin/servers.json", "compose.lan.yaml",
                     "compose.users.lan.yaml", "LICENSE", "NOTICE", "docs/install.md", "docs/keycloak.md"):
        contents[filename] = (root / filename).read_bytes()
    name = f"iceberg-data-platform-{version}-install"
    archive = output / f"{name}.tar.gz"
    with (
        archive.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as compressed,
        tarfile.open(fileobj=compressed, mode="w") as tar,
    ):
        for filename, data in sorted(contents.items()):
            info = tarfile.TarInfo(f"{name}/{filename}")
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            tar.addfile(info, io.BytesIO(data))
    installers = [output / filename for filename in ("install.sh", "install.ps1")]
    for installer in installers:
        installer.write_bytes(contents[installer.name])
    archives = [*sorted(output.glob(f"iceberg-data-platform-{version}*.tar.gz")), *installers]
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in archives
    ))
    print(f"Built {archive.name} ({archive.stat().st_size:,} bytes)")
    return archive


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", action="store_true", help="Also build dist/source archive and checksum")
    parser.add_argument("--install", action="store_true", help="Also build a Docker-only installation bundle")
    parser.add_argument("--release-tag", default="latest", help="GitHub release to download from the generated installers")
    parser.add_argument("--image-tag", default="latest", help="Matching application image tag for the installation bundle")
    parser.add_argument("--channel", metavar="REF", help="Only print the release names for a Git ref as key=value lines")
    args = parser.parse_args()
    try:
        if args.channel:
            version = json.loads((ROOT / "package.json").read_text())["version"]
            channel = release_channel(args.channel, version, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
            print("\n".join(f"{key}={value}" for key, value in channel.items()))
            return
        build() if args.build else check()
        if args.install:
            build_install(release_tag=args.release_tag, image_tag=args.image_tag)
    except ValueError as exc:
        parser.exit(1, f"Release check failed: {exc}\n")


if __name__ == "__main__":
    main()
