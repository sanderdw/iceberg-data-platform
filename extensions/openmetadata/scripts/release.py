"""Release checks and the source archive of the OpenMetadata extension, independent of the platform's release.

  uv run python -m scripts.release              # check versions, files and secrets
  uv run python -m scripts.release --tag openmetadata-v0.1.0 --build
  uv run python -m scripts.release --build --install   # also the installation bundle and install.sh
  uv run python -m scripts.release --channel refs/heads/main   # release names for a Git ref (in Actions)
"""

import argparse
import gzip
import hashlib
import io
import os
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".sh", ".js", ".css", ".html", ".svg", ".md", ".yml", ".yaml", ".sql", ".csv", ".toml", ".lock",
            ".txt", ".ttf", ".example", ".json"}
PLAIN = {"Dockerfile", ".gitignore", ".dockerignore", ".python-version", "LICENSE", "NOTICE"}
BINARY = {".ttf"}
SECRET = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|BRIDGE_CLIENT_SECRET=[0-9a-f]{16,}"
                    r"|OM_JWT_TOKEN=ey[A-Za-z0-9_-]{20,}")
IMAGE = "iceberg-openmetadata"
# What the installer puts in the platform's extensions/openmetadata, next to the compose file.
INSTALL_FILES = ("compose.openmetadata.yaml", "scripts/setup.py", "scripts/om_token.py", "LICENSE", "NOTICE")
TAG = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}")
# The platform's documentation names the installer of this version.
DOCS = ROOT.parent.parent / "docs" / "openmetadata.md"


def tracked(root=ROOT):
    output = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "."]).decode()
    return sorted(Path(p) for p in output.split("\0") if p)


def check(root=ROOT, tag=None, docs=DOCS):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if f'VERSION = "{version}"' not in (root / "openmetadata_sync" / "config.py").read_text():
        raise SystemExit("openmetadata_sync/config.py VERSION differs from pyproject.toml")
    if f"## {version}" not in (root / "CHANGELOG.md").read_text():
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    for compose in ("compose.yaml",):
        text = (root / compose).read_text()
        if f"iceberg-openmetadata:{version}" not in text:
            raise SystemExit(f"{compose} does not use the {version} images")
    for page in (root / "README.md", docs):
        if page.exists() and f"/openmetadata-v{version}/install.sh" not in page.read_text():
            raise SystemExit(f"{page} does not install openmetadata-v{version}")
    if tag is not None and tag != f"openmetadata-v{version}":
        raise SystemExit(f"Tag {tag} does not match version openmetadata-v{version}")
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
    name = f"iceberg-openmetadata-{version}"
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


def install_compose(root, version, image_tag):
    """compose.yaml on the published image. A strict text rewrite rather than `docker compose config`,
    which would inline the env files (the bridge secret and the bot token) of a local checkout."""
    text = (root / "compose.yaml").read_text()
    local = f"    image: {IMAGE}:{version}\n"
    if text.count(local) != 1:
        raise SystemExit(f"compose.yaml must use {IMAGE}:{version} once")
    text = text.replace(local, f"    image: ghcr.io/sanderdw/{IMAGE}:{image_tag}\n")
    text, builds = re.subn(r"(?m)^    build: \S+\n", "", text)
    if builds != 1 or "build:" in text:
        raise SystemExit("Unexpected build sections in compose.yaml")
    return text.encode()


def installer_source(root, release_tag):
    """install.sh that downloads from its own release (never releases/latest: that is the platform's)."""
    content = (root / "install.sh").read_text()
    if content.count("    release_tag=unreleased\n") != 1:
        raise SystemExit("install.sh must set release_tag=unreleased once")
    return content.replace("    release_tag=unreleased\n", f"    release_tag={release_tag}\n").encode()


def local_secrets(root):
    """Credential values in local env files, to make sure no released file contains them."""
    values = []
    for env_file in root.glob(".env*"):
        if env_file.is_file() and env_file.name != ".env.example":
            for line in env_file.read_text().splitlines():
                key, _, value = line.partition("=")
                if ("SECRET" in key or "TOKEN" in key) and len(value.strip()) >= 12:
                    values.append(value.strip().strip("\"'"))
    return values


def build_install(root, version, release_tag, image_tag):
    """The installation bundle, install.sh for this release and SHA256SUMS over the release's files."""
    for value in (release_tag, image_tag):
        if not TAG.fullmatch(value):
            raise SystemExit("Invalid release or image tag")
    contents = {"compose.yaml": install_compose(root, version, image_tag),
                **{name: (root / name).read_bytes() for name in INSTALL_FILES}}
    installer = installer_source(root, release_tag)
    for secret in local_secrets(root):
        if any(secret.encode() in data for data in (*contents.values(), installer)):
            raise SystemExit("A local credential is in a bundled file; value suppressed")
    name = f"iceberg-openmetadata-{version}-install"
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"{name}.tar.gz"
    with archive.open("wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", filename="", mtime=0) as zipped, \
            tarfile.open(fileobj=zipped, mode="w") as tar:
        for filename, data in sorted(contents.items()):
            info = tarfile.TarInfo(f"{name}/{filename}")
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    (output / "install.sh").write_bytes(installer)
    files = [*sorted(output.glob(f"iceberg-openmetadata-{version}*.tar.gz")), output / "install.sh"]
    (output / "SHA256SUMS").write_text("".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in files))
    return archive


def channel(ref, version, run_id, attempt):
    """An openmetadata-vX.Y.Z tag is a release; a branch is a preview with its own image and release tags."""
    if ref == f"refs/tags/openmetadata-v{version}":
        return {"version": version, "preview": "false", "image_tag": version, "release_tag": f"openmetadata-v{version}",
                "slug": ""}
    if not ref.startswith("refs/heads/"):
        raise SystemExit(f"Run this workflow on a branch or on tag openmetadata-v{version}, not {ref}")
    # The same names as the platform's branch previews, so one branch's images line up.
    slug = re.sub(r"[^a-z0-9._-]", "-", ref.removeprefix("refs/heads/").lower())[:60].strip("-.")
    if not slug or re.match(r"v\d", slug):
        raise SystemExit("This branch name cannot be used for a preview")
    build = f"{slug}-{run_id}-{attempt}"
    return {"version": version, "preview": "true", "image_tag": build, "release_tag": f"openmetadata-{build}",
            "slug": slug}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--install", action="store_true", help="Also build the installation bundle and install.sh")
    parser.add_argument("--release-tag", help="The release install.sh downloads from (default: openmetadata-vVERSION)")
    parser.add_argument("--image-tag", help="The image the bundle runs (default: VERSION)")
    parser.add_argument("--channel", metavar="REF", help="Only print the release names for a Git ref")
    args = parser.parse_args()
    if args.channel:
        version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
        names = channel(args.channel, version, os.environ["GITHUB_RUN_ID"], os.environ["GITHUB_RUN_ATTEMPT"])
        print("\n".join(f"{key}={value}" for key, value in names.items()))
        return
    version, files = check(tag=args.tag)
    print(f"PASS: iceberg-openmetadata {version}, {len(files)} files")
    if args.build:
        print(build(ROOT, version, files))
    if args.install:
        print(build_install(ROOT, version, args.release_tag or f"openmetadata-v{version}", args.image_tag or version))


if __name__ == "__main__":
    main()
