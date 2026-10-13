"""Release names for a tag or a branch preview."""

import pytest

from scripts.release import channel


def test_a_version_tag_is_a_release_and_a_branch_a_preview():
    assert channel("refs/tags/dbt-v0.1.0", "0.1.0", "7", "1") == {
        "version": "0.1.0", "preview": "false", "image_tag": "0.1.0", "release_tag": "dbt-v0.1.0", "slug": ""}
    assert channel("refs/heads/Feature/Edit", "0.1.0", "7", "2") == {
        "version": "0.1.0", "preview": "true", "image_tag": "feature-edit-7-2", "release_tag": "dbt-feature-edit-7-2",
        "slug": "feature-edit"}


@pytest.mark.parametrize("ref", ["refs/tags/dbt-v0.2.0", "refs/tags/v0.8.0", "refs/pull/24/merge",
                                 "refs/heads/v1-hotfix", "refs/heads/---"])
def test_other_refs_are_refused(ref):
    with pytest.raises(SystemExit):
        channel(ref, "0.1.0", "7", "1")
