"""Release names, and the release scan that finds LLM credentials before they are published."""

import pytest

from scripts.release import SECRET, channel


@pytest.mark.parametrize("text", ["ANTHROPIC_API_KEY=" + "a" * 20, "AWS_BEARER_TOKEN_BEDROCK=" + "t" * 30,
                                  "AKIA" + "A" * 16, "sk-ant-" + "a" * 40, "sk-proj-" + "a" * 40])
def test_llm_credentials_are_secrets(text):
    assert SECRET.search(text)


def test_words_are_not_secrets():
    assert not SECRET.search("disk-" + "a" * 40)
    assert not SECRET.search("LLM_MODEL=anthropic:claude-sonnet-5-5")


def test_a_version_tag_is_a_release_and_a_branch_a_preview():
    assert channel("refs/tags/conversationalbi-v0.1.0", "0.1.0", "7", "1") == {
        "version": "0.1.0", "preview": "false", "image_tag": "0.1.0", "release_tag": "conversationalbi-v0.1.0",
        "slug": ""}
    assert channel("refs/heads/Feature/Chat", "0.1.0", "7", "2") == {
        "version": "0.1.0", "preview": "true", "image_tag": "feature-chat-7-2",
        "release_tag": "conversationalbi-feature-chat-7-2", "slug": "feature-chat"}


@pytest.mark.parametrize("ref", ["refs/tags/conversationalbi-v0.2.0", "refs/tags/v0.7.0", "refs/pull/24/merge",
                                 "refs/heads/v1-hotfix", "refs/heads/---"])
def test_other_refs_are_refused(ref):
    with pytest.raises(SystemExit):
        channel(ref, "0.1.0", "7", "1")
