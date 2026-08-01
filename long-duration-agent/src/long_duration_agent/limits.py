"""Input/output size limits enforced before any model call or storage write."""

from __future__ import annotations

from .config import get_settings


class InputTooLargeError(ValueError):
    def __init__(self, char_count: int, limit: int) -> None:
        self.char_count = char_count
        self.limit = limit
        super().__init__(f"Prompt has {char_count} characters, which exceeds the limit of {limit}.")


class ArtifactTooLargeError(ValueError):
    def __init__(self, size_bytes: int, limit: int) -> None:
        self.size_bytes = size_bytes
        self.limit = limit
        super().__init__(f"Artifact is {size_bytes} bytes, which exceeds the limit of {limit}.")


def validate_prompt_length(prompt: str) -> None:
    limit = get_settings().lda_max_input_chars
    if len(prompt) > limit:
        raise InputTooLargeError(len(prompt), limit)
    if not prompt.strip():
        raise ValueError("Prompt must not be empty.")


def validate_markdown_size(markdown: str) -> None:
    limit = get_settings().lda_max_markdown_bytes
    size = len(markdown.encode("utf-8"))
    if size > limit:
        raise ArtifactTooLargeError(size, limit)
