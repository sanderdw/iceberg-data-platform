"""Release checks and the source archive of Conversational BI, independent of the platform's release.

  uv run python -m scripts.release              # check versions, files and secrets
  uv run python -m scripts.release --tag conversationalbi-v0.1.0 --build
"""

import argparse
import gzip
import io
import re
import subprocess
import tarfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIXES = {".py", ".js", ".mjs", ".ts", ".tsx", ".css", ".html", ".svg", ".md", ".yml", ".yaml", ".toml", ".lock",
            ".txt", ".ttf", ".example", ".json", ".parquet"}
PLAIN = {"Dockerfile", ".gitignore", ".dockerignore", ".python-version", "LICENSE", "NOTICE"}
BINARY = {".ttf", ".parquet"}
SECRET = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|BRIDGE_CLIENT_SECRET=[0-9a-f]{16,}|"
                    r"(?:GOOGLE|GEMINI|ANTHROPIC|OPENAI)_API_KEY=[A-Za-z0-9_.-]{16,}|AIza[0-9A-Za-z_-]{30,}|"
                    r"AWS_BEARER_TOKEN_BEDROCK=[A-Za-z0-9_.+/=-]{16,}|\b(?:AKIA|ASIA)[0-9A-Z]{16}\b|"
                    r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}|RUNTIME_KEY=[0-9a-f]{16,}")


def tracked(root=ROOT):
    output = subprocess.check_output(["git", "-C", str(root), "ls-files", "-z", "."]).decode()
    return sorted(Path(p) for p in output.split("\0") if p)


def check(root=ROOT, tag=None):
    version = tomllib.loads((root / "pyproject.toml").read_text())["project"]["version"]
    if f'VERSION = "{version}"' not in (root / "conversationalbi" / "config.py").read_text():
        raise SystemExit("conversationalbi/config.py VERSION differs from pyproject.toml")
    for package in ("web/package.json", "runtime/package.json"):
        if f'"version": "{version}"' not in (root / package).read_text():
            raise SystemExit(f"{package} version differs from pyproject.toml")
    if f"## {version}" not in (root / "CHANGELOG.md").read_text():
        raise SystemExit(f"CHANGELOG.md has no section for {version}")
    for compose in ("compose.yaml",):
        text = (root / compose).read_text()
        images = (f"iceberg-conversationalbi:{version}", f"iceberg-conversationalbi-runtime:{version}")
        if not all(image in text for image in images):
            raise SystemExit(f"{compose} does not use the {version} images")
    if tag is not None and tag != f"conversationalbi-v{version}":
        raise SystemExit(f"Tag {tag} does not match version conversationalbi-v{version}")
    files = []
    for path in tracked(root):
        full = root / path
        if (path.name.startswith(".env") and path.name != ".env.example") or path.name == "llm.env":
            raise SystemExit(f"An environment file is tracked: {path}")
        if path.suffix not in SUFFIXES and path.name not in PLAIN:
            raise SystemExit(f"Unexpected file type: {path}")
        if path.suffix not in BINARY and SECRET.search(full.read_text(errors="replace")):
            raise SystemExit(f"Possible secret in {path}")
        files.append(path)
    return version, files


def build(root, version, files):
    name = f"iceberg-conversationalbi-{version}"
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag")
    parser.add_argument("--build", action="store_true")
    args = parser.parse_args()
    version, files = check(tag=args.tag)
    print(f"PASS: iceberg-conversationalbi {version}, {len(files)} files")
    if args.build:
        print(build(ROOT, version, files))


if __name__ == "__main__":
    main()
