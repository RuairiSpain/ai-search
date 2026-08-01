"""Parse a composition expression like 'guard(sequence(10,05),04)' into a Node.

Robust against malformed input: unbalanced parentheses, trailing characters,
unknown operators, unknown pattern ids (when a catalogue is supplied), and
pathological nesting depth. Raises ValueError with a clear message rather than
leaking a low-level exception or silently accepting nonsense.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .models import Node

_OPS = {"sequence", "nest", "guard", "fan", "substitute"}
_MAX_DEPTH = 20
_MAX_LEN = 2000


def parse_composition(expr: str, cat: Catalogue | None = None,
                      _depth: int = 0) -> Node:
    if _depth > _MAX_DEPTH:
        raise ValueError("composition nested too deeply")
    if expr is None:
        raise ValueError("composition is empty")
    expr = expr.strip()
    if not expr:
        raise ValueError("composition is empty")
    if len(expr) > _MAX_LEN:
        raise ValueError("composition expression too long")

    if "(" not in expr:
        leaf = expr
        if leaf.endswith(")") or "," in leaf:
            raise ValueError(f"malformed composition near '{leaf}'")
        if cat is not None and leaf not in cat.patterns:
            raise ValueError(f"unknown pattern id '{leaf}'")
        return Node.leaf(leaf)

    op = expr[:expr.index("(")].strip()
    if op not in _OPS:
        raise ValueError(
            f"unknown operator '{op}' (expected one of {sorted(_OPS)})")
    if not expr.endswith(")"):
        raise ValueError(f"unbalanced or trailing characters in '{expr}'")

    inner = expr[expr.index("(") + 1:-1]
    parts, depth, cur = [], 0, ""
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                raise ValueError(f"unbalanced parentheses in '{expr}'")
        if ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if depth != 0:
        raise ValueError(f"unbalanced parentheses in '{expr}'")
    parts.append(cur)                       # always keep the final segment
    if any(p.strip() == "" for p in parts):  # e.g. 'nest(05,)' or 'sequence()'
        raise ValueError(f"empty operand in '{expr}'")

    _check_arity(op, len(parts), expr)
    children = [parse_composition(p, cat, _depth + 1) for p in parts]
    return Node.op(op, *children)


def _check_arity(op: str, n: int, expr: str) -> None:
    """sequence/fan take 2+; nest/guard/substitute take exactly 2."""
    if op in ("nest", "guard", "substitute") and n != 2:
        raise ValueError(f"operator '{op}' takes exactly 2 operands, got {n} in '{expr}'")
    if op in ("sequence", "fan") and n < 2:
        raise ValueError(f"operator '{op}' takes 2 or more operands, got {n} in '{expr}'")
