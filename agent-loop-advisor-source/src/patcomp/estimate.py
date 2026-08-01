"""Stage 8 — cost and latency estimation.

Arithmetic over budget_profile only. A model-generated cost figure is a build
failure: it makes the user's choice uninformed and the tool actively harmful.

Compounding: sequence sums, nest multiplies inner by outer rounds, fan
multiplies by branch count. The confidence band widens with composition depth
AND with the fraction of the IR that was assumed rather than stated.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .models import IR, Candidate, Node

# Illustrative unit economics. Deliberately explicit so they can be replaced
# with a tenant's real rate card; never model-generated.
COST_PER_1K_TOKENS = 0.002        # blended
SECONDS_PER_CALL = 3.0


def _rollup(node: Node, cat: Catalogue) -> tuple[int, int, float]:
    """Returns (llm_calls, tokens, wall_clock_s) for a subtree."""
    if node.is_leaf:
        p = cat.pattern(node.pattern)          # type: ignore[arg-type]
        return p.llm_calls, p.tokens, float(p.wall_clock_s)

    if node.operator == "nest" and len(node.children) == 2:
        outer, inner = node.children
        oc, ot, ow = _rollup(outer, cat)
        ic, it, iw = _rollup(inner, cat)
        rounds = 1
        if outer.is_leaf:
            rounds = max(1, cat.pattern(outer.pattern).loop_rounds)   # type: ignore[arg-type]
        return oc + ic * rounds, ot + it * rounds, ow + iw * rounds

    if node.operator == "fan" and len(node.children) == 2:
        plan, worker = node.children
        pc, pt, pw = _rollup(plan, cat)
        wc, wt, ww = _rollup(worker, cat)
        branches = 3
        return pc + wc * branches, pt + wt * branches, pw + ww

    # sequence, guard, substitute: additive; guard/substitute are cheap wrappers
    calls = tokens = 0
    secs = 0.0
    for c in node.children:
        cc, ct, cw = _rollup(c, cat)
        calls += cc
        tokens += ct
        secs += cw
    return calls, tokens, secs


def estimate(cand: Candidate, ir: IR, cat: Catalogue) -> Candidate:
    calls, tokens, secs = _rollup(cand.tree, cat)
    cand.cost_per_task = round(tokens / 1000.0 * COST_PER_1K_TOKENS, 4)
    cand.latency_s = round(max(secs, calls * SECONDS_PER_CALL), 1)

    # Confidence band: base, widened by depth and by the assumed fraction.
    depth_penalty = 0.10 * max(0, cand.tree.depth() - 1)
    unknown_penalty = 0.5 * ir.unknown_ratio
    cand.confidence_band = round(min(0.90, 0.20 + depth_penalty + unknown_penalty), 2)
    return cand


def estimate_all(cands: list[Candidate], ir: IR, cat: Catalogue) -> list[Candidate]:
    return [estimate(c, ir, cat) for c in cands]


def band_note(cand: Candidate, ir: IR) -> str:
    assumed = len(ir.unknowns)
    total = len(ir.all_fields)
    pct = int(cand.confidence_band * 100)
    if total:
        return f"±{pct}% — {assumed} of {total} requirement fields assumed"
    return f"±{pct}%"
