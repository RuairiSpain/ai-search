"""Small text-processing helpers shared across patterns.

`strip_code_fences` existed as two separate, near-identical inline
implementations (pattern 01's optimize.py, pattern 11's workflow.py as
`_strip_fences`) before being extracted here — the review that flagged
`_strip_fences` as untested also implicitly flagged the duplication; fixing
both at once.
"""
from __future__ import annotations


def strip_code_fences(text: str) -> str:
    """Strip a single leading/trailing markdown code fence if present, e.g.
    turns "```python\ncode\n```" into "code\n". Models asked for raw source
    or raw markdown frequently wrap it in a fence anyway; this undoes that.

    Only strips a fence that STARTS the text (after whitespace trimming) —
    a fence appearing later in the text (e.g. as part of an explanation) is
    left alone, since stripping on any "```" occurrence would mangle content
    that legitimately contains fenced examples.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    # Discard the opening fence line (with any language tag, e.g. ```python)
    _, _, rest = stripped.partition("\n")
    if "```" in rest:
        rest = rest.rsplit("```", 1)[0]
    return rest.strip()
