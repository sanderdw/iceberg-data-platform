"""The release scan finds LLM credentials before they are published."""

import pytest

from scripts.release import SECRET


@pytest.mark.parametrize("text", ["ANTHROPIC_API_KEY=" + "a" * 20, "AWS_BEARER_TOKEN_BEDROCK=" + "t" * 30,
                                  "AKIA" + "A" * 16, "sk-ant-" + "a" * 40, "sk-proj-" + "a" * 40])
def test_llm_credentials_are_secrets(text):
    assert SECRET.search(text)


def test_words_are_not_secrets():
    assert not SECRET.search("disk-" + "a" * 40)
    assert not SECRET.search("LLM_MODEL=anthropic:claude-sonnet-5-5")
