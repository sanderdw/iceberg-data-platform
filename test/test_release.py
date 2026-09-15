"""The public artifact must preserve source and reject accidental private additions."""

import hashlib
import json
import secrets
import shutil
import tarfile

import pytest

from scripts.release import ROOT, build, build_install, check, release_files


@pytest.fixture
def release_tree(tmp_path):
    root = tmp_path / "source"
    for path in release_files():
        target = root / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    return root


def test_source_archive_is_reproducible_and_excludes_local_state(release_tree):
    (release_tree / ".env").write_text("LOCAL_PASSWORD=" + secrets.token_hex(24))
    (release_tree / ".venv").mkdir()
    (release_tree / ".venv" / "private.txt").write_text("private data")
    (release_tree / "docs" / "conversation.md").write_text("Private local development conversation")
    first = build(release_tree)
    digest = hashlib.sha256(first.read_bytes()).hexdigest()
    second = build(release_tree)
    assert hashlib.sha256(second.read_bytes()).hexdigest() == digest
    with tarfile.open(first) as archive:
        members = archive.getnames()
        assert any(name.endswith("/.env.example") for name in members)
        assert any(name.endswith("/LICENSE") for name in members)
        assert any(name.endswith("/01_pyiceberg_neighborhood.py") for name in members)
        pgadmin = next(name for name in members if name.endswith("/pgadmin/servers.json"))
        assert archive.extractfile(pgadmin).read() == (release_tree / "pgadmin" / "servers.json").read_bytes()
        screenshot = next(name for name in members if name.endswith("/docs/portal.png"))
        assert archive.extractfile(screenshot).read() == (release_tree / "docs" / "portal.png").read_bytes()
        assert not any(name.endswith("/.env") or "/.venv/" in name or "/dist/" in name for name in members)
        assert not any(name.endswith("/docs/conversation.md") for name in members)


def test_known_local_secret_in_public_source_is_rejected(release_tree):
    secret = secrets.token_hex(24)
    (release_tree / ".env").write_text("LOCAL_PASSWORD=" + secret)
    (release_tree / "server" / "accidental.py").write_text("credential = " + repr(secret))
    with pytest.raises(ValueError, match="Local credential") as exc:
        check(release_tree)
    assert secret not in str(exc.value)


def test_symlink_and_unexpected_private_file_are_rejected(release_tree, tmp_path):
    private = release_tree / "user_portal" / "key.json"
    private.write_text("{}")
    with pytest.raises(ValueError, match="Unexpected file"):
        check(release_tree)
    private.unlink()
    outside = tmp_path / "outside.txt"
    outside.write_text("private")
    (release_tree / "docs" / "linked.md").symlink_to(outside)
    with pytest.raises(ValueError, match="Symlinks"):
        check(release_tree)


@pytest.mark.skipif(shutil.which("docker") is None, reason="Docker Compose is required to render the bundle")
def test_install_bundle_is_portable_versioned_and_preserves_secrets(release_tree):
    secret = secrets.token_hex(24)
    (release_tree / ".env").write_text(f"PORTAL_PASSWORD={secret}\n")
    source = build(release_tree)
    bundle = build_install(release_tree)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert hashlib.sha256(build_install(release_tree).read_bytes()).hexdigest() == digest
    with tarfile.open(bundle) as archive:
        files = {name.split("/", 1)[1]: archive.extractfile(name).read() for name in archive.getnames()}
    assert ".env" not in files
    assert "scripts/setup.py" in files
    assert "pgadmin/servers.json" in files
    for content in files.values():
        assert secret.encode() not in content
        assert str(release_tree).encode() not in content
    admin = json.loads(files["compose.yaml"])
    users = json.loads(files["compose.users.yaml"])
    assert admin["name"] == "iceberg-platform"
    assert users["name"] == "iceberg-workspaces"
    for model in (admin, users):
        assert all("build" not in service for service in model["services"].values())
    assert admin["services"]["portal"]["image"] == admin["services"]["monitor"]["image"]
    notebook_image = users["services"]["notebook-image"]["image"]
    assert users["services"]["users"]["environment"]["NOTEBOOK_IMAGE"] == notebook_image
    version = json.loads((release_tree / "package.json").read_text())["version"]
    assert notebook_image == f"ghcr.io/sanderdw/iceberg-data-platform-notebook:{version}"
    assert admin["services"]["portal"]["environment"]["PORTAL_PASSWORD"] == "${PORTAL_PASSWORD}"
    mounts = admin["services"]["pgadmin"]["volumes"]
    assert any(mount.get("source") == "./pgadmin/servers.json" for mount in mounts)
    checksums = (bundle.parent / "SHA256SUMS").read_text()
    for path in (source, bundle):
        assert f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" in checksums
