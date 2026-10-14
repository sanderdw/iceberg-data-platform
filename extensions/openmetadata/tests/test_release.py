"""Release names for a tag or a branch preview, and the extension's own setup."""

import os
import stat

import pytest

from scripts.release import channel
from scripts.setup import read, setup


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
