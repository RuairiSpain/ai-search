"""Stage 5 — candidate generation.

Breadth cheaply, then kill for free. Three is a presentation constraint, not a
reasoning constraint, so generate six to eight along declared axes and let the
deterministic stage reject most of them.

Candidate zero (the grounded baseline) is never generated. It is always present
as the falsifiability test every other candidate must beat.
"""
from __future__ import annotations

from itertools import combinations

from .catalogue import Catalogue
from .models import IR, Candidate, Node

# Fixed axes. Users who run the tool twice learn the axis once; options that
# differ along whatever axis the model picked that day cannot be compared.
MINIMAL, BALANCED, AMBITIOUS = "minimal", "balanced", "ambitious"

# Ordering for a sequence: context patterns first, then reasoning, then process.
ROLE_ORDER = {
    "context": 0, "planning": 1, "reasoning": 2, "action": 3,
    "orchestration": 4, "artefact": 5, "process": 6, "improvement": 7,
    "guard": 8, "meta": 9, "baseline": 10,
}


def baseline(cat: Catalogue) -> Candidate:
    return Candidate(Node.leaf(cat.baseline_id), axis="baseline")


def _needs_gate(ir: IR) -> bool:
    return bool(ir.needs_approval or ir.human_in_reasoning or ir.binds_writes)


def _needs_guard(ir: IR) -> bool:
    # A rules guard is warranted both when policy compliance is diagnosed and
    # when the architect declared a task class that must be deterministic —
    # otherwise generation can produce only candidates that the determinism
    # boundary rule will kill, forcing an unnecessary scaffold.
    if ir.must_be_deterministic:
        return True
    return any(s.signature_id == "deterministic_policy_compliance"
               for s in ir.diagnosed)


def _ordered(patterns: list[str], cat: Catalogue) -> list[str]:
    return sorted(patterns, key=lambda p: (ROLE_ORDER.get(cat.pattern(p).role, 5), p))


def _seq(patterns: list[str], cat: Catalogue) -> Node:
    ordered = _ordered(patterns, cat)
    if len(ordered) == 1:
        return Node.leaf(ordered[0])
    return Node.op("sequence", *[Node.leaf(p) for p in ordered])


def _wrap(node: Node, ir: IR, cat: Catalogue, prefer: str | None = None) -> Node:
    """Apply a guard or gate over the action boundary when the IR demands one."""
    if prefer == "04" or (_needs_guard(ir) and "04" not in node.patterns()):
        return Node.op("guard", node, Node.leaf("04"))
    if _needs_gate(ir) and "13" not in node.patterns() and "08" not in node.patterns():
        return Node.op("guard", node, Node.leaf("13"))
    return node


def candidates(ir: IR, cat: Catalogue, width: int = 8) -> list[Candidate]:
    """Six to eight candidates along the declared axes."""
    sigs = [s for s in ir.reasoning_signatures]
    wanted: list[str] = []
    for s in sigs:
        if s.pattern and s.pattern not in wanted:
            wanted.append(s.pattern)

    if not wanted:
        return []

    out: list[Candidate] = []
    seen: set[str] = set()

    def add(tree: Node, axis: str):
        c = Candidate(tree, axis=axis)
        if c.signature in seen:
            return
        seen.add(c.signature)
        out.append(c)

    # --- MINIMAL: the single highest-signal pattern, guarded only if required
    primary = wanted[0]
    add(_wrap(Node.leaf(primary), ir, cat), MINIMAL)
    if len(wanted) > 1:
        add(_wrap(Node.leaf(wanted[1]), ir, cat), MINIMAL)

    # --- BALANCED: the diagnosed patterns composed, plus governance
    if len(wanted) >= 2:
        for combo in combinations(wanted, min(2, len(wanted))):
            add(_wrap(_seq(list(combo), cat), ir, cat), BALANCED)
    if len(wanted) >= 3:
        add(_wrap(_seq(wanted[:3], cat), ir, cat), BALANCED)

    # --- When the diagnosis names fewer patterns than we have axes, expand
    # using the catalogue's own composes_with. The expansion is never invented:
    # a pattern may only be paired with one it declares itself composable with.
    if len(wanted) == 1:
        partners = [q for q in cat.pattern(primary).composes_with
                    if q in cat.patterns and q not in ("13", "04")]
        # prefer a partner that adds governance or context, cheapest first
        partners.sort(key=lambda q: cat.pattern(q).budget_profile.get("tokens", 0))
        for q in partners[:2]:
            add(_wrap(_seq([primary, q], cat), ir, cat), BALANCED)
        add(_wrap(Node.leaf(primary), ir, cat, prefer="04"), BALANCED)

    # --- AMBITIOUS: everything diagnosed, nesting where a decision node exists
    if len(wanted) >= 2:
        outer, inner = wanted[0], wanted[1]
        if cat.pattern(outer).has_decision_nodes:
            add(_wrap(Node.op("nest", Node.leaf(outer), Node.leaf(inner)), ir, cat), AMBITIOUS)
        elif cat.pattern(inner).has_decision_nodes:
            add(_wrap(Node.op("nest", Node.leaf(inner), Node.leaf(outer)), ir, cat), AMBITIOUS)
    if len(wanted) >= 2:
        add(_wrap(_seq(wanted, cat), ir, cat), AMBITIOUS)
    # a governed variant of the primary, for contrast
    add(_wrap(Node.leaf(primary), ir, cat, prefer="04"), AMBITIOUS)

    return out[:width]
