"""Parses the pseudo-JSON-schema DSL used throughout this repo's
instructions.md files and ad-hoc prompts (e.g. `{"score": 0-10, "reason":
str}`, `{"verdict": "approve"|"revise"|"reject", ...}`) and synthesizes a
plausible fake value matching it.

Why a parser instead of 26 hardcoded fake responses: every pattern's
instructions.md ALREADY documents its exact output contract in this DSL
(that's what the model reads too). Hand-writing a fake per role would drift
out of sync with the real prompts the moment either changes. Reading the
same text the model reads keeps the fake backend and the real contracts
tied to one source of truth.

This is NOT a JSON parser — the DSL uses bare type names (`str`, `bool`,
`int`, `0-1`), literal-alternation (`"a"|"b"|"c"`), and placeholder
containers (`{}`, `[]`, `{...}`) that aren't valid JSON values.
"""
from __future__ import annotations

import re

_PREFERRED_LITERALS = {
    # key name -> preferred literal when multiple options are offered, so the
    # fake backend's "happy path" default steers toward the branch most
    # instructions.md files expect on a clean first pass. Deliberately does
    # NOT include "action": both p05 (call|resolved) and p10
    # (get_entity|get_neighbors|find_paths|conclude) use that key name for
    # DIFFERENT option sets — a single preferred value can't be valid for
    # both, and "resolved" isn't even a legal p10 option. Falling through to
    # options[0] for "action" is also more useful: it exercises real tool
    # dispatch (a call/get_entity hop) instead of resolving immediately.
    "verdict": "approve",
    "recommendation": "pay",
    "provisional_recommendation": "proceed_with_conditions",
}


def find_schema_block(text: str) -> str | None:
    """Find the LAST top-level {...} block in text that looks like this
    repo's schema DSL (quoted keys followed by a colon). Instructions.md
    files put the real output schema last, after any example/rules text."""
    depth = 0
    start = None
    candidates: list[str] = []
    in_str = False
    for i, ch in enumerate(text):
        if ch == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        if in_str:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start:i + 1])
                start = None
    schema_like = [c for c in candidates if re.search(r'"\w+"\s*:', c)]
    return schema_like[-1] if schema_like else None


def _split_top_level(inner: str) -> list[str]:
    """Split on top-level commas only (respecting nested {}/[]/quotes)."""
    parts, depth, in_str, buf = [], 0, False, []
    for i, ch in enumerate(inner):
        if ch == '"' and (i == 0 or inner[i - 1] != "\\"):
            in_str = not in_str
        if not in_str:
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
            elif ch == "," and depth == 0:
                parts.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _split_key_value(pair: str) -> tuple[str, str] | None:
    """Split 'key: value' on the first top-level colon (not inside quotes)."""
    depth, in_str = 0, False
    for i, ch in enumerate(pair):
        if ch == '"' and (i == 0 or pair[i - 1] != "\\"):
            in_str = not in_str
        if not in_str:
            if ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
            elif ch == ":" and depth == 0:
                return pair[:i].strip().strip('"'), pair[i + 1:].strip()
    return None


def _fake_scalar(spec: str, key: str) -> object:
    low = spec.strip().lower().rstrip(".")
    if re.match(r"^-?\d+(\.\d+)?-\d+(\.\d+)?$", low):  # a range like 0-1, 0-10
        if "confidence" in key:
            return 0.75
        if "risk" in key or "score" in key:
            return 6
        return 5
    if low in ("bool", "boolean"):
        return True
    if low in ("int", "integer", "number", "float", "n"):
        if "confidence" in key:
            return 0.75
        return 5
    if low.startswith('"') and low.endswith('"'):
        return spec.strip().strip('"')
    return f"fake-{key or 'value'}"


def fake_value(spec: str, key: str = "") -> object:
    spec = spec.strip().rstrip(",")
    # Container checks MUST come before the literal-alternation check below:
    # a list-of-objects value like [{"kind": "a"|"b"|"c", ...}] contains its
    # OWN top-level `|` and plenty of quotes, so checking "is this a flat
    # literal alternation" first would wrongly flatten the whole nested
    # object into a garbled string (found by testing against the real
    # planner schema in patterns/03 — subtasks came back as a truncated
    # string instead of a list of dicts).
    if spec.startswith("[") and spec.endswith("]"):
        inner = spec[1:-1].strip().rstrip(",").strip()
        if not inner or inner == "...":
            return [f"fake-{key}-item"] if key else []
        if inner.startswith("{"):
            return [fake_object(inner)]
        if inner.startswith("["):
            # list of lists, e.g. [["S1","S4"], ["S2"], ...] — synthesize a
            # small plausible nested list rather than flattening it.
            return [[f"X{i}"] for i in (1, 2)]
        if inner.startswith('"') and "|" not in inner:
            return [f"X{i}" for i in range(1, 3)]
        return [fake_value(inner, key)]
    if spec.startswith("{"):
        if spec.strip("{} \t\n") in ("", "..."):
            return {}
        return fake_object(spec)
    if "|" in spec and spec.count('"') >= 4:
        options = [o.strip().strip('"') for o in spec.split("|")]
        return _pick_literal(key, options)
    return _fake_scalar(spec, key)


def _pick_literal(key: str, options: list[str]) -> str:
    preferred = _PREFERRED_LITERALS.get(key)
    return preferred if preferred in options else options[0]


def fake_object(schema_text: str) -> dict:
    """schema_text is a {...} block (braces included or not); returns a dict
    matching it as best as the DSL can be parsed."""
    text = schema_text.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    out: dict = {}
    for pair in _split_top_level(text):
        kv = _split_key_value(pair)
        if not kv:
            continue
        key, value_spec = kv
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        out[key] = fake_value(value_spec, key)
    return out


def synthesize(text: str) -> dict | None:
    """Top-level entry point: find the schema block in `text` (typically a
    system prompt) and synthesize a fake dict matching it. Returns None if no
    schema-like block is found (caller should fall back to plain text)."""
    block = find_schema_block(text)
    if block is None:
        return None
    obj = fake_object(block)
    return obj or None
