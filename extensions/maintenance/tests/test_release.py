"""Release names for a tag or a branch preview, and the extension's own setup."""

import os
import stat

import pytest

from scripts.release import ROOT, channel, check
from scripts.setup import read, setup


def test_a_version_tag_is_a_release_and_a_branch_a_preview():
    assert channel("refs/tags/maintenance-v0.0.1", "0.0.1", "7", "1") == {
        "version": "0.0.1", "preview": "false", "image_tag": "0.0.1", "release_tag": "maintenance-v0.0.1", "slug": "",
        "chart_version": "0.0.1"}
    assert channel("refs/heads/Feature/Edit", "0.0.1", "7", "2") == {
        "version": "0.0.1", "preview": "true", "image_tag": "feature-edit-7-2",
        "release_tag": "maintenance-feature-edit-7-2", "slug": "feature-edit",
        "chart_version": "0.0.1-feature-edit-7-2"}


def test_a_preview_chart_version_is_semver():
    # Helm refuses the underscores and dots a branch image tag keeps.
    names = channel("refs/heads/feature_k8s.2", "0.0.1", "7", "1")
    assert (names["image_tag"], names["chart_version"]) == ("feature_k8s.2-7-1", "0.0.1-feature-k8s-2-7-1")


def test_a_chart_with_another_version_is_refused(tmp_path):
    for name in ("pyproject.toml", "CHANGELOG.md", "compose.yaml", "maintenance/config.py", "runner/main.go",
                 "contracts/maintenance-api/v1/openapi.yaml", "deploy/helm/iceberg-maintenance/Chart.yaml"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text((ROOT / name).read_text())
    chart = tmp_path / "deploy/helm/iceberg-maintenance/Chart.yaml"
    chart.write_text(chart.read_text().replace("\nversion: ", "\nversion: 9.9.9-was-"))
    with pytest.raises(SystemExit, match=r"Chart\.yaml has another version"):
        check(tmp_path)


@pytest.mark.parametrize("ref", ["refs/tags/maintenance-v0.2.0", "refs/tags/v0.9.0", "refs/pull/24/merge",
                                 "refs/heads/v1-hotfix", "refs/heads/---"])
def test_other_refs_are_refused(ref):
    with pytest.raises(SystemExit):
        channel(ref, "0.0.1", "7", "1")


def test_setup_writes_a_private_launcher_token_and_keeps_it(tmp_path):
    with pytest.raises(SystemExit):
        setup(tmp_path)
    (tmp_path / ".env.bridge").write_text("BRIDGE_CONTRACT=1\nEXTENSION_ORIGIN=http://localhost:3013\n"
                                          "PLATFORM_NETWORK=iceberg-platform_default\n")
    setup(tmp_path)
    first = read(tmp_path / ".env")
    assert len(first["LAUNCHER_TOKEN"]) == 48 and first["MNT_PORT"] == "3013"
    assert first["PLATFORM_NETWORK"] == "iceberg-platform_default"
    assert stat.S_IMODE(os.stat(tmp_path / ".env").st_mode) == 0o600
    setup(tmp_path)
    assert read(tmp_path / ".env") == first
