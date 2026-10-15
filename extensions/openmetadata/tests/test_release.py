"""Release names for a tag or a branch preview, and the extension's own setup."""

import hashlib
import os
import shutil
import stat
import tarfile
from pathlib import Path

import pytest

from scripts.release import INSTALL_FILES, build_install, channel, check
from scripts.setup import read, setup

ROOT = Path(__file__).resolve().parent.parent


def test_a_version_tag_is_a_release_and_a_branch_a_preview():
    assert channel("refs/tags/openmetadata-v0.1.0", "0.1.0", "7", "1") == {
        "version": "0.1.0", "preview": "false", "image_tag": "0.1.0", "release_tag": "openmetadata-v0.1.0",
        "slug": ""}
    assert channel("refs/heads/Feature/Edit", "0.1.0", "7", "2") == {
        "version": "0.1.0", "preview": "true", "image_tag": "feature-edit-7-2",
        "release_tag": "openmetadata-feature-edit-7-2", "slug": "feature-edit"}


@pytest.mark.parametrize("ref", ["refs/tags/openmetadata-v0.2.0", "refs/tags/v0.9.1", "refs/pull/24/merge",
                                 "refs/heads/v1-hotfix", "refs/heads/---"])
def test_other_refs_are_refused(ref):
    with pytest.raises(SystemExit):
        channel(ref, "0.1.0", "7", "1")


def test_setup_keeps_openmetadata_settings(tmp_path):
    with pytest.raises(SystemExit):
        setup(tmp_path)
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3011\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    setup(tmp_path)
    first = read(tmp_path / ".env")
    assert first["OM_PORT"] == "3011" and first["PLATFORM_NETWORK"] == "iceberg-platform_default"
    assert first["OM_URL"] == "" and first["OM_SERVICE_PREFIX"] == "iceberg"
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    (tmp_path / ".env").write_text((tmp_path / ".env").read_text().replace("OM_URL=", "OM_URL=http://om:8585/api"))
    setup(tmp_path)
    assert read(tmp_path / ".env") == {**first, "OM_URL": "http://om:8585/api"}


def test_setup_takes_settings_from_the_environment(tmp_path):
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3011\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    (tmp_path / ".env").write_text("OM_URL=http://old:8585/api\nOM_DOMAIN=Sales\nCUSTOM=kept\n")
    setup(tmp_path, env={"OM_URL": "https://om.example.com/api/", "OM_JWT_TOKEN": "eyJ.token"}, interactive=False)
    values = read(tmp_path / ".env")
    assert values["OM_URL"] == "https://om.example.com/api" and values["OM_JWT_TOKEN"] == "eyJ.token"
    assert values["OM_DOMAIN"] == "Sales" and values["CUSTOM"] == "kept"
    with pytest.raises(SystemExit, match="http"):
        setup(tmp_path, env={"OM_URL": "om.example.com"}, interactive=False)


def test_setup_asks_for_missing_settings_in_a_terminal(tmp_path):
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3011\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    answers = iter(["https://om.example.com/api", "https://om.example.com"])
    setup(tmp_path, env={}, interactive=True, ask=lambda _: "eyJ.secret", prompt=lambda _: next(answers))
    values = read(tmp_path / ".env")
    assert (values["OM_URL"], values["OM_JWT_TOKEN"], values["OM_UI_URL"]) == (
        "https://om.example.com/api", "eyJ.secret", "https://om.example.com")
    # Configured: a rerun asks nothing.
    setup(tmp_path, env={}, interactive=True, ask=None, prompt=None)


def test_setup_demo_selects_the_local_openmetadata(tmp_path):
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3011\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    (tmp_path / ".env").write_text("OM_URL=https://om.example.com/api\nOM_UI_URL=https://om.example.com\n")
    # Settings of another OpenMetadata in the environment do not apply to the local one.
    env = {"OM_URL": "https://om.example.com/api", "OM_JWT_TOKEN": "eyJ.bot", "OM_UI_URL": "https://om.example.com"}
    setup(tmp_path, env=env, demo=True, interactive=True, ask=None, prompt=None)
    values = read(tmp_path / ".env")
    assert values["COMPOSE_FILE"] == "compose.yaml:compose.openmetadata.yaml"
    # compose.openmetadata.yaml supplies the local addresses when these are empty.
    assert values["OM_URL"] == "" and values["OM_UI_URL"] == "" and values["OM_JWT_TOKEN"] == ""
    # A rerun without --demo keeps the local OpenMetadata and asks nothing.
    setup(tmp_path, env=env, interactive=True, ask=None, prompt=None)
    assert read(tmp_path / ".env") == values


@pytest.fixture
def release_root(tmp_path):
    root = tmp_path / "openmetadata"
    for name in ("pyproject.toml", "compose.yaml", "install.sh", "README.md", *INSTALL_FILES):
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, root / name)
    return root


def test_the_install_bundle_runs_the_published_image(release_root):
    archive = build_install(release_root, "0.1.1", "openmetadata-v0.1.1", "0.1.1")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    assert hashlib.sha256(build_install(release_root, "0.1.1", "openmetadata-v0.1.1", "0.1.1")
                          .read_bytes()).hexdigest() == digest
    with tarfile.open(archive) as tar:
        files = {name.split("/", 1)[1]: tar.extractfile(name).read() for name in tar.getnames()}
    assert set(files) == {"compose.yaml", *INSTALL_FILES}
    compose = files["compose.yaml"].decode()
    assert "    image: ghcr.io/sanderdw/iceberg-openmetadata:0.1.1\n" in compose and "build:" not in compose
    assert all(files[name] == (release_root / name).read_bytes() for name in INSTALL_FILES)
    installer = (archive.parent / "install.sh").read_text()
    assert "    release_tag=openmetadata-v0.1.1\n" in installer and "release_tag=unreleased" not in installer
    sums = dict(reversed(line.split("  ")) for line in (archive.parent / "SHA256SUMS").read_text().splitlines())
    assert sums == {archive.name: digest,
                    "install.sh": hashlib.sha256((archive.parent / "install.sh").read_bytes()).hexdigest()}
    preview = build_install(release_root, "0.1.1", "openmetadata-main-7-1", "main-7-1")
    with tarfile.open(preview) as tar:
        compose = tar.extractfile("iceberg-openmetadata-0.1.1-install/compose.yaml").read().decode()
    assert "iceberg-openmetadata:main-7-1\n" in compose


def test_the_install_bundle_refuses_unsafe_tags_and_other_images(release_root):
    with pytest.raises(SystemExit, match="tag"):
        build_install(release_root, "0.1.1", "openmetadata-v0.1.1", "x;rm -rf /")
    with pytest.raises(SystemExit, match=r"0\.1\.2"):
        build_install(release_root, "0.1.2", "openmetadata-v0.1.2", "0.1.2")


def test_the_install_bundle_excludes_local_credentials(release_root):
    secret = "eyJ" + "a" * 40
    (release_root / ".env").write_text(f"OM_JWT_TOKEN={secret}\n")
    (release_root / "scripts/om_token.py").write_text(f"# {secret}\n")
    with pytest.raises(SystemExit, match="credential"):
        build_install(release_root, "0.1.1", "openmetadata-v0.1.1", "0.1.1")


def test_the_documentation_names_this_release_installer(release_root, tmp_path):
    docs = tmp_path / "openmetadata.md"
    docs.write_text("curl -fsSL https://github.com/sanderdw/iceberg-data-platform/releases/download/"
                    "openmetadata-v0.1.0/install.sh | sh\n")
    with pytest.raises(SystemExit, match=r"openmetadata-v0\.1\.1"):
        check(ROOT, docs=docs)
    check(ROOT)
