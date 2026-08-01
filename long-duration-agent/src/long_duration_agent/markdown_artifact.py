"""Builds the bilingual Markdown artifact saved for each translation request."""

from __future__ import annotations

from datetime import datetime


def _yaml_escape(value: str) -> str:
    return value.replace('"', '\\"').replace("\n", " ")


def build_markdown(
    *,
    artifact_id: str,
    created_at: datetime,
    english_text: str,
    spanish_text: str,
) -> str:
    """Renders the artifact as YAML front-matter + English/Spanish sections.

    The original English text is preserved exactly (verbatim, inside a
    Markdown quote-free section); the Spanish text is the model's plain-text
    translation, not re-parsed as Markdown.
    """
    front_matter = "\n".join(
        [
            "---",
            f'artifact_id: "{_yaml_escape(artifact_id)}"',
            f'created_utc: "{created_at.strftime("%Y-%m-%dT%H:%M:%SZ")}"',
            'source_language: "en"',
            'target_language: "es-ES"',
            "---",
        ]
    )
    return (
        f"{front_matter}\n\n"
        "# Original English Text\n\n"
        f"{english_text.strip()}\n\n"
        "---\n\n"
        "# Traducción al Español (España)\n\n"
        f"{spanish_text.strip()}\n"
    )
