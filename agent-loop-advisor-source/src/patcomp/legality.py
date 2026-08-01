"""Stage 6 — the constraint kill.

Free. Deterministic. No model, no tokens. Runs before any critique spend, so
illegal candidates die for nothing (§7: cheap breadth, free constraint kill,
then deepen the survivors).

Phase 1 rules are pure structure. Phase 2 rules need the IR and budget
arithmetic. Both are free; the split exists so the ordering is provable.
"""
from __future__ import annotations

from .catalogue import Catalogue, Pattern
from .models import IR, Candidate, Kill, Node

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _producer_type(node: Node, cat: Catalogue) -> str:
    """The contract type a subtree emits."""
    if node.is_leaf:
        return cat.pattern(node.pattern).produces      # type: ignore[arg-type]
    if node.operator == "guard":
        # guard(target, guard_pattern): the guarded target's type flows on
        return _producer_type(node.children[0], cat)
    if node.operator == "substitute":
        return _producer_type(node.children[0], cat)
    if node.operator in ("sequence", "fan"):
        return _producer_type(node.children[-1], cat)
    if node.operator == "nest":
        return _producer_type(node.children[0], cat)   # outer owns the output
    return ""


def _join_ok(frm: str, to_pattern: Pattern, cat: Catalogue) -> tuple[bool, str]:
    """contract_join: direct | subsumes | adapter. No adapter, no join."""
    if frm in to_pattern.accepts:
        return True, "direct"
    for s in cat.subsumes or []:
        if s.get("from") == frm and s.get("to") in to_pattern.accepts:
            return True, "subsumes"
    for accepted in to_pattern.accepts:
        if cat.adapter_between(frm, accepted):
            return True, f"adapter:{cat.adapter_between(frm, accepted).id}"  # type: ignore[union-attr]
    return False, ""


def _iter_joins(node: Node, cat: Catalogue):
    """Yield (producer_type, consumer_pattern) for every sequence/fan joint."""
    if node.is_leaf:
        return
    if node.operator in ("sequence", "fan"):
        for i in range(len(node.children) - 1):
            left, right = node.children[i], node.children[i + 1]
            for consumer in right.leaves()[:1]:
                yield _producer_type(left, cat), cat.pattern(consumer.pattern)  # type: ignore[arg-type]
    for c in node.children:
        yield from _iter_joins(c, cat)


def _nest_pairs(node: Node):
    if node.is_leaf:
        return
    if node.operator == "nest" and len(node.children) == 2:
        outer, inner = node.children
        if outer.is_leaf and inner.is_leaf:
            yield outer.pattern, inner.pattern
    for c in node.children:
        yield from _nest_pairs(c)


def _guard_targets(node: Node):
    """Yield (guarded_subtree, guard_pattern_id)."""
    if node.is_leaf:
        return
    if node.operator == "guard" and len(node.children) == 2:
        yield node.children[0], node.children[1]
    for c in node.children:
        yield from _guard_targets(c)


def _covered_by_guard(cand: Candidate, cat: Catalogue) -> bool:
    """Is any write boundary wrapped by a guard or human gate?"""
    for _target, guard in _guard_targets(cand.tree):
        for leaf in guard.leaves():
            p = cat.pattern(leaf.pattern)              # type: ignore[arg-type]
            if p.is_guard or p.is_human_gate:
                return True
    # A gate inside a process pattern (08 carries human_gate) also governs.
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        if p.is_human_gate and p.id != "13":
            return True
    return False


# --------------------------------------------------------------------------
# Phase 1 — structural. Pure tree + catalogue. No IR needed beyond tools.
# --------------------------------------------------------------------------


def check_contract_join(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    kills = []
    for frm, consumer in _iter_joins(cand.tree, cat):
        if not frm:
            continue
        ok, _how = _join_ok(frm, consumer, cat)
        if not ok:
            kills.append(Kill(
                "contract_join", 1,
                f"{frm} cannot feed pattern {consumer.id} (accepts {', '.join(consumer.accepts)})",
                repair=f"declare an adapter {frm}->one of {consumer.accepts} in contracts.yaml",
            ))
    return kills


def check_terminal_is_last(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    kills = []

    def walk(node: Node, is_last: bool):
        if node.is_leaf:
            t = cat.type_of(cat.pattern(node.pattern).produces)  # type: ignore[arg-type]
            if t and t.is_terminal and not is_last:
                kills.append(Kill(
                    "terminal_is_last", 1,
                    f"pattern {node.pattern} produces terminal type {t.name} mid-composition",
                    repair="move it to a composition leaf",
                ))
            return
        if node.operator in ("sequence", "fan"):
            for i, c in enumerate(node.children):
                walk(c, is_last and i == len(node.children) - 1)
        else:
            for i, c in enumerate(node.children):
                walk(c, is_last and i == 0)

    walk(cand.tree, True)
    return kills


# Question and Case are the ambient entry points of any harness: a requirements
# document always implies one or the other, so they never need explicit binding.
AMBIENT = {"Question", "Case", "Spec"}


def check_source_origin_bound(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """A pattern whose ONLY way in is an unbound external source has a joint
    with no upstream and will fail at run time.

    Accepting Evidence is not the same as requiring it: 05 accepts
    [Question, Case, Evidence, GraphContext] and runs on a Case alone. The rule
    fires only when every accepted type is an external source with no origin.
    """
    kills = []
    produced = {cat.pattern(p).produces for p in cand.tree.patterns()}
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        satisfiable = [
            a for a in p.accepts
            if a in AMBIENT or a in produced or a in ir.source_origins
        ]
        if p.accepts and not satisfiable:
            missing = ", ".join(p.accepts)
            kills.append(Kill(
                "source_origin_bound", 1,
                f"pattern {pid} accepts only {missing}, none of which is bound or produced",
                repair=f"bind one of [{missing}] in the IR, or drop pattern {pid}",
            ))
    return kills


def check_bounded_loop(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    kills = []
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        if p.has_loop and not p.budget_profile:
            kills.append(Kill("bounded_loop", 1,
                              f"pattern {pid} loops with no budget", repair="declare a budget"))
    return kills


def check_write_is_governed(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    binds = ir.binds_writes or any(
        cat.pattern(p).binds_write_tools for p in cand.tree.patterns())
    if not binds:
        return []
    if _covered_by_guard(cand, cat):
        return []
    return [Kill("write_is_governed", 1,
                 "composition binds write-capable tools with no guard or human gate",
                 repair="wrap the write boundary with guard(…, 04) or guard(…, 13)")]


def check_tool_hygiene(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """Tool-facing patterns must carry tool-hygiene; observations are evidence,
    never directives."""
    kills = []
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        tool_facing = pid in ("02", "09", "10") or p.binds_write_tools
        if tool_facing and "tool-hygiene" not in p.skills:
            kills.append(Kill("tool_hygiene_emitted", 1,
                              f"pattern {pid} is tool-facing without tool-hygiene",
                              repair="emit tool-hygiene into the tool-facing agents"))
    return kills


def check_decision_fields(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    kills = []
    required = {"verdict", "rationale", "rejected_alternatives", "confidence"}
    dt = cat.type_of("Decision")
    if not dt:
        return []
    missing = required - set(dt.fields)
    if missing:
        for pid in cand.tree.patterns():
            if cat.pattern(pid).produces == "Decision":
                kills.append(Kill("decision_fields_present", 1,
                                  f"Decision type missing mandatory fields: {sorted(missing)}"))
                break
    return kills


def check_conflicts(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """conflict_table — no pair may match a fatal conflicts.yaml entry."""
    kills: list[Kill] = []
    present = set(cand.tree.patterns())
    nests = list(_nest_pairs(cand.tree))

    for c in cat.conflicts:
        if c.a not in present:
            continue
        # condition guards
        if c.when == "sensitive_data_present" and not ir.sensitive_data:
            continue
        if c.when == "writes_bound" and not (
                ir.binds_writes or any(cat.pattern(p).binds_write_tools for p in present)):
            continue
        if c.when == "weak_tests" and not ir.weak_tests:
            continue
        if c.when == "outer_is_autonomous_loop":
            # 07 is only in conflict when nothing reviews the skill boundary
            if _covered_by_guard(cand, cat):
                continue
        if c.when == "no_entity_resolution_policy":
            if "entity-resolution" in cat.pattern(c.a).skills:
                continue

        applies = False
        if c.relation == "nest":
            applies = any(a == c.a and b == c.b for a, b in nests) or \
                      (c.b == "*" and any(a == c.a for a, _ in nests))
        elif c.relation in ("any", "sequence", "substitute"):
            applies = c.b == "*" or c.b in present

        if not applies:
            continue
        # `unless` satisfied by a guard for the write/review cases
        if c.unless and "guard" in (c.unless or "") and _covered_by_guard(cand, cat):
            continue
        if c.when == "writes_bound" and _covered_by_guard(cand, cat):
            continue

        kill = Kill("conflict_table", 1, c.reason.replace("\n", " ").strip(), repair=c.repair)
        if c.severity == "fatal":
            kills.append(kill)
        else:
            cand.warnings.append(kill)
    return kills


# --------------------------------------------------------------------------
# Phase 2 — envelopes. Needs the IR. Still free (arithmetic + lookups).
# --------------------------------------------------------------------------


def check_budget_funded(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """A nested pattern's budget must fit the enclosing loop's per-iteration
    allowance."""
    kills = []
    for outer_id, inner_id in _nest_pairs(cand.tree):
        outer, inner = cat.pattern(outer_id), cat.pattern(inner_id)
        rounds = max(1, outer.loop_rounds)
        if inner.llm_calls * rounds > outer.llm_calls * 4:
            kills.append(Kill(
                "budget_funded", 2,
                f"pattern {inner_id} ({inner.llm_calls} calls x {rounds} rounds) "
                f"cannot be funded inside {outer_id} ({outer.llm_calls} calls)",
                repair="declare a joint budget ceiling and reduce branch width",
            ))
    return kills


def check_determinism_boundary(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """A must-be-deterministic task class may not have an LLM on its path."""
    if not ir.must_be_deterministic:
        return []
    has_det_core = any(cat.pattern(p).is_deterministic_core for p in cand.tree.patterns())
    if has_det_core:
        return []
    return [Kill(
        "determinism_boundary_respected", 2,
        f"task classes {ir.must_be_deterministic} must be deterministic but no "
        f"pattern in the composition provides a deterministic core",
        repair="add pattern 04 (rules engine) or 08 (state machine) to own that path",
    )]


def check_residency(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    return []      # every catalogue service is available in declared regions today


def check_evaluator_named(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """§3 — the evaluator is the architecture."""
    if ir.evaluator_named:
        return []
    return [Kill("evaluator_named", 2,
                 "no evaluator named for the primary task class",
                 repair="declare what 'good' means before building")]


# --------------------------------------------------------------------------
# Phase 3 — presentation legality. Whether a survivor is fit to show a user.
# --------------------------------------------------------------------------


def check_beats_baseline_stated(cand: Candidate, ir: IR, cat: Catalogue) -> list[Kill]:
    """beats_baseline_stated (operators.yaml, fatal) — the structural cure for
    pattern astrology: every non-baseline candidate must carry a satisfied
    beats_baseline_when statement, or it is not presented."""
    if cand.tree.patterns() == [cat.baseline_id]:
        return []       # the baseline itself is exempt — it IS the floor
    if any(cat.pattern(pid).beats_baseline_when.strip() for pid in cand.tree.patterns()):
        return []
    return [Kill(
        "beats_baseline_stated", 3,
        f"no pattern in {cand.signature} states why it beats the grounded baseline",
        repair="drop the candidate; if none survive, the baseline is the answer",
    )]


PHASE_1 = [
    check_contract_join, check_source_origin_bound, check_terminal_is_last,
    check_bounded_loop, check_write_is_governed, check_tool_hygiene,
    check_decision_fields, check_conflicts,
]
PHASE_2 = [
    check_budget_funded, check_determinism_boundary, check_residency,
    check_evaluator_named,
]
PHASE_3 = [
    check_beats_baseline_stated,
]


def kill(cand: Candidate, ir: IR, cat: Catalogue, phases=(1, 2, 3)) -> Candidate:
    checks = []
    if 1 in phases:
        checks += PHASE_1
    if 2 in phases:
        checks += PHASE_2
    if 3 in phases:
        checks += PHASE_3
    for check in checks:
        cand.kills.extend(check(cand, ir, cat))
    return cand


def kill_all(cands: list[Candidate], ir: IR, cat: Catalogue,
             phases=(1, 2, 3)) -> tuple[list[Candidate], list[tuple[str, Kill]]]:
    """Returns (survivors, kill_log)."""
    log: list[tuple[str, Kill]] = []
    survivors = []
    for c in cands:
        kill(c, ir, cat, phases)
        if c.alive:
            survivors.append(c)
        else:
            for k in c.kills:
                log.append((c.signature, k))
    return survivors, log
