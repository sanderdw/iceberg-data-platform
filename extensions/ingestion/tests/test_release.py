"""Release names for a tag or a branch preview, and the extension's own setup."""

import os
import stat

import pytest

from scripts.release import channel
from scripts.setup import read, setup


def test_a_version_tag_is_a_release_and_a_branch_a_preview():
    assert channel("refs/tags/ingestion-v0.1.0", "0.1.0", "7", "1") == {
        "version": "0.1.0", "preview": "false", "image_tag": "0.1.0", "release_tag": "ingestion-v0.1.0",
        "slug": "", "chart_version": "0.1.0"}
    assert channel("refs/heads/Feature/Edit", "0.1.0", "7", "2") == {
        "version": "0.1.0", "preview": "true", "image_tag": "feature-edit-7-2",
        "release_tag": "ingestion-feature-edit-7-2", "slug": "feature-edit", "chart_version": "0.1.0-feature-edit-7-2"}


def test_a_preview_chart_version_is_semver():
    # Helm refuses the underscores and dots a branch image tag keeps.
    names = channel("refs/heads/feature_k8s.2", "0.1.0", "7", "1")
    assert (names["image_tag"], names["chart_version"]) == ("feature_k8s.2-7-1", "0.1.0-feature-k8s-2-7-1")


@pytest.mark.parametrize("ref", ["refs/tags/ingestion-v0.2.0", "refs/tags/v0.9.0", "refs/pull/24/merge",
                                 "refs/heads/v1-hotfix", "refs/heads/---"])
def test_other_refs_are_refused(ref):
    with pytest.raises(SystemExit):
        channel(ref, "0.1.0", "7", "1")


def test_setup_writes_private_keys_and_keeps_them(tmp_path):
    with pytest.raises(SystemExit):
        setup(tmp_path)
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3008\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    setup(tmp_path)
    first = read(tmp_path / ".env")
    assert len(bytes.fromhex(first["ING_SECRET_KEY"])) == 32 and len(first["LAUNCHER_TOKEN"]) == 48
    assert first["ING_PORT"] == "3008" and first["PLATFORM_NETWORK"] == "iceberg-platform_default"
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    setup(tmp_path)
    assert read(tmp_path / ".env") == first
