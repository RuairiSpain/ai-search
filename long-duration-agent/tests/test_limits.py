import pytest

from long_duration_agent.limits import InputTooLargeError, validate_prompt_length


def test_accepts_prompt_within_limit():
    validate_prompt_length("hello world")


def test_rejects_empty_prompt():
    with pytest.raises(ValueError):
        validate_prompt_length("   ")


def test_rejects_prompt_over_one_million_characters():
    with pytest.raises(InputTooLargeError) as exc_info:
        validate_prompt_length("a" * 1_000_001)
    assert exc_info.value.limit == 1_000_000
    assert exc_info.value.char_count == 1_000_001


def test_accepts_prompt_at_exactly_the_limit():
    validate_prompt_length("a" * 1_000_000)
