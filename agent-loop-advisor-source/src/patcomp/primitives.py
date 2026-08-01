"""Tier-2 primitive scaffolds.

When no catalogue composition legally fits, the paper's own instruction applies:
compose from the primitives. Nothing pre-built exists to verify against, so the
result is UNVERIFIED by construction and says so.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .models import IR, Scaffold

# §1's middle layer: the verbs every pattern is built from.
PRIMITIVE_LOOPS = {
    "generate_alternatives": "generate N candidates -> score against criteria -> select one -> record why the others lost",
    "branch_hypotheses": "hold K hypotheses -> test each against evidence -> prune -> synthesise the survivor",
    "search_a_space": "expand breadth cheaply -> apply hard constraints -> deepen only the survivors",
    "act_and_observe": "thought -> action -> observation, repeated under an explicit call budget",
    "constrain": "model proposes -> deterministic engine decides -> engine's verdict wins",
    "remember": "retrieve scoped memory -> use -> write back with a TTL",
    "reflect_and_learn": "run -> critique the trajectory -> author a lesson -> review gate -> apply next run",
    "coordinate_roles": "plan -> fan out to workers -> different-family review -> merge",
    "verify": "produce artefact -> run the checker -> analyse failure -> repair, under a cap",
}

PRIMITIVE_EVALUATORS = {
    "generate_alternatives": "model judge over the selection, calibrated against human labels",
    "branch_hypotheses": "trajectory evaluator — judge the pruning steps, not just the verdict",
    "search_a_space": "rule-based feasibility check on each candidate plan (free)",
    "act_and_observe": "trajectory evaluator over tool-call correctness",
    "constrain": "rule-based — the engine IS the evaluator (free)",
    "remember": "retrieval precision on recalled items, plus a staleness check",
    "reflect_and_learn": "hybrid — human review of authored skills, plus next-run delta",
    "coordinate_roles": "different-family model judge over merged output",
    "verify": "test-based — the test suite is the evaluator (free, cannot be flattered)",
}

PRIMITIVE_DEPENDENCIES = {
    "generate_alternatives": ["a scoring rubric written down before the build"],
    "branch_hypotheses": ["an explicit branch budget (K) and a prune criterion"],
    "search_a_space": ["hard constraints expressible as code", "a cheap model for breadth"],
    "act_and_observe": ["tool bindings, read-only by default", "injection-aware observation handling"],
    "constrain": ["the policy, expressed as executable rules", "a rules engine host"],
    "remember": ["a vector or table store", "a security-trimming policy and TTL"],
    "reflect_and_learn": ["a versioned skill library", "a human review gate over promotion"],
    "coordinate_roles": ["typed handoff contracts", "a serial merge queue for shared state"],
    "verify": ["an executable test suite", "a sandbox to run it in"],
}


def build(ir: IR, cat: Catalogue, reasons: list[str]) -> Scaffold:
    """Compose a scaffold from the primitives the diagnosis implies."""
    prims: list[str] = []
    for sig in ir.reasoning_signatures:
        if not sig.pattern:
            continue
        for p in cat.pattern(sig.pattern).primitives:
            if p not in prims:
                prims.append(p)

    # Governance primitives the IR demands, even with no pattern behind them.
    if (ir.must_be_deterministic or any(
            s.signature_id == "deterministic_policy_compliance" for s in ir.diagnosed)) \
            and "constrain" not in prims:
        prims.append("constrain")
    if not prims:
        prims = ["generate_alternatives"]
    if ir.needs_approval or ir.human_in_reasoning:
        if "verify" not in prims:
            prims.append("verify")

    loops = [f"{p}: {PRIMITIVE_LOOPS[p]}" for p in prims if p in PRIMITIVE_LOOPS]
    evals = [f"{p}: {PRIMITIVE_EVALUATORS[p]}" for p in prims if p in PRIMITIVE_EVALUATORS]
    deps: list[str] = []
    for p in prims:
        for d in PRIMITIVE_DEPENDENCIES.get(p, []):
            if d not in deps:
                deps.append(d)
    if ir.binds_writes:
        deps.append("READ-ONLY tool bindings only — write access is not emitted at this tier")
    if ir.needs_approval or ir.human_in_reasoning:
        deps.append("a human approval step with an SLA and an escalation path")

    problems = ", ".join(s.problem for s in ir.reasoning_signatures) or "an undiagnosed reasoning need"
    return Scaffold(
        primitives=prims,
        loops=loops,
        evaluators=evals,
        dependencies=deps,
        rationale=(
            f"Diagnosis found {problems}, but no catalogue composition covers it "
            f"legally. Composed from primitives instead."
        ),
        unverified_reasons=reasons,
    )
