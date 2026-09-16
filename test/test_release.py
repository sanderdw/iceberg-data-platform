"""The public artifact must preserve source and reject accidental private additions."""

import hashlib
import json
import secrets
import shutil
import subprocess
import sys
import tarfile

import pytest

from scripts.release import ROOT, build, build_install, check, installer_source, release_files


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


@pytest.mark.parametrize("env_file", [".env", ".env.private"])
def test_known_local_secret_in_public_source_is_rejected(release_tree, env_file):
    secret = secrets.token_hex(24)
    (release_tree / env_file).write_text("LOCAL_PASSWORD=" + secret)
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
@pytest.mark.parametrize("tag", ["latest", "keycloak-123-1"])
def test_install_bundle_is_portable_and_excludes_secrets(release_tree, tag):
    secret = secrets.token_hex(24)
    (release_tree / ".env").write_text(f"PORTAL_PASSWORD={secret}\n")
    source = build(release_tree)
    bundle = build_install(release_tree, release_tag=tag, image_tag=tag)
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert hashlib.sha256(build_install(release_tree, release_tag=tag, image_tag=tag).read_bytes()).hexdigest() == digest
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
    assert "compose.keycloak.yaml" not in files
    assert "users" not in admin["services"]
    assert "keycloak" not in users["services"]
    assert admin["services"]["polaris"]["environment"]["POLARIS_AUTHENTICATION_TYPE"] == "mixed"
    assert admin["services"]["keycloak-bootstrap"]["image"] == admin["services"]["portal"]["image"]
    assert admin["name"] == "iceberg-platform"
    assert users["name"] == "iceberg-workspaces"
    for model in (admin, users):
        assert all("build" not in service for service in model["services"].values())
    assert admin["services"]["portal"]["image"] == admin["services"]["monitor"]["image"]
    notebook_image = users["services"]["notebook-image"]["image"]
    assert users["services"]["users"]["environment"]["NOTEBOOK_IMAGE"] == notebook_image
    assert notebook_image == f"ghcr.io/sanderdw/iceberg-data-platform-notebook:{tag}"
    for model in (admin, users):
        for service in model["services"].values():
            if service["image"].startswith("ghcr.io/sanderdw/"):
                assert service["image"].endswith(":" + tag)
    for filename in ("install.sh", "install.ps1"):
        assert files[filename] == (bundle.parent / filename).read_bytes()
        assert files[filename] == installer_source(release_tree, filename, tag, tag)
        if tag == "latest":
            assert files[filename] == (release_tree / filename).read_bytes()
        else:
            assert b":latest" not in files[filename]
            assert tag.encode() in files[filename]
    assert admin["services"]["portal"]["environment"]["PORTAL_PASSWORD"] == "${PORTAL_PASSWORD}"
    mounts = admin["services"]["pgadmin"]["volumes"]
    assert any(mount.get("source") == "./pgadmin/servers.json" for mount in mounts)
    checksums = (bundle.parent / "SHA256SUMS").read_text()
    for path in (source, bundle, bundle.parent / "install.sh", bundle.parent / "install.ps1"):
        assert f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" in checksums
    # Resolve the distributed configuration only after generating its private env.
    # In particular, the realm import must remain a bind mount through rendering.
    installed = release_tree.parent / "installed"
    for name, content in files.items():
        target = installed / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    subprocess.run([sys.executable, str(installed / "scripts/setup.py")], check=True)
    resolved = []
    for filename, expected in (("compose.yaml", "iceberg-platform"), ("compose.users.yaml", "iceberg-workspaces")):
        result = subprocess.check_output(["docker", "compose", "--env-file", str(installed / ".env"),
                                          "-f", str(installed / filename), "config", "--format", "json"])
        model = json.loads(result)
        assert model["name"] == expected
        resolved.append(model)
    platform_model, workspace_model = resolved
    assert platform_model["networks"]["default"]["name"] == workspace_model["networks"]["catalog"]["name"]
    assert platform_model["volumes"]["keycloak-data"]["name"] == "iceberg-platform_keycloak-data"


@pytest.mark.parametrize("release_tag,image_tag", [
    ("keycloak-123-1", "latest"), ("latest", "keycloak-123-1"),
    ("bad;tag", "keycloak"), ("keycloak", "bad$(command)"),
])
def test_installer_rejects_mixed_channels_and_unsafe_tags(release_tag, image_tag):
    with pytest.raises(ValueError):
        installer_source(ROOT, "install.sh", release_tag, image_tag)
