"""Turn Result objects into JSON-serialisable dicts for MCP tool output."""
from __future__ import annotations

from patcomp.catalogue import Catalogue
from patcomp.models import Candidate, Outcome, Result
from patcomp import present
from patcomp import diagram


def _card(cand: Candidate, others: list[Candidate], cat: Catalogue) -> dict:
    return {
        "axis": cand.axis,
        "composition": cand.tree.signature(),
        "composition_plain": present.chips(cand, cat),
        "patterns": cand.tree.patterns(),
        "what_it_does": cat.pattern(cand.tree.patterns()[0]).summary.strip().splitlines()[0],
        "beats_baseline_because": present.beats_baseline(cand, cat),
        "cost_per_task_eur": cand.cost_per_task,
        "latency_seconds": cand.latency_s,
        "confidence_band": cand.confidence_band,
        "humans_sit": present.humans_sit(cand, cat),
        "top_risk": present.top_risk(cand, cat),
        "tradeoff": present.tradeoff(cand, others, cat),
        "warnings": [w.reason.strip() for w in cand.warnings],
        "recommended": cand.recommended,
        "diagram_mermaid": diagram.composition_mermaid(cand.tree, cat),
    }


def _baseline(cand: Candidate | None) -> dict | None:
    if cand is None:
        return None
    return {
        "composition": "00",
        "what_it_does": "one grounded agent, no orchestration",
        "cost_per_task_eur": cand.cost_per_task,
        "latency_seconds": cand.latency_s,
    }


def result_to_dict(result: Result, cat: Catalogue) -> dict:
    ir = result.ir
    out: dict = {
        "outcome": result.outcome.value,
        "tier": result.tier,
        "verified": result.verified,
        "confidence": result.confidence.value,
        "descent_reason": result.descent_reason,
        "headline": _headline(result),
        "diagnosis": [
            {"signature": s.signature_id, "problem": s.problem,
             "pattern": s.pattern, "prior_score": round(s.prior_score, 2),
             "source": ("interview" if s.user_label is not None
                        else "model" if s.model_label is not None else "prior")}
            for s in (ir.diagnosed if ir else [])
        ],
        "questions_to_ask": result.questions,
        "baseline": _baseline(result.baseline),
    }
    if result.outcome.value in ("baseline_recommended", "baseline_fallback"):
        out["diagram_markdown"] = diagram.pattern_markdown("00", cat)

    if result.outcome is Outcome.THREE_CARDS:
        out["cards"] = [_card(c, result.candidates, cat) for c in result.candidates]
        rec = next((c for c in result.candidates if c.recommended),
                   result.candidates[0])
        out["recommended_axis"] = rec.axis
        out["diagram_markdown"] = diagram.composition_markdown(
            rec.tree, cat, "Recommended composition (Balanced)")

    if result.outcome is Outcome.PRIMITIVE_SCAFFOLD and result.scaffold:
        sc = result.scaffold
        out["scaffold"] = {
            "unverified": True,
            "rationale": sc.rationale,
            "primitives": sc.primitives,
            "suggested_loops": sc.loops,
            "evaluators": sc.evaluators,
            "dependencies": sc.dependencies,
            "not_verified_because": sc.unverified_reasons,
            "cost_shown": False,
            "note": "No cost figure: a primitive composition has no budget profile, "
                    "and inventing one is forbidden. Bound read-only.",
            "diagram_mermaid": diagram.primitives_mermaid(sc.primitives),
        }

    # the kill log is the most useful telemetry the system produces
    out["rejected"] = [
        {"composition": sig, "killed_by": k.rule_id,
         "reason": k.reason.strip()[:200], "repair": (k.repair or "").strip()[:200]}
        for sig, k in result.kill_log[:12]
    ]
    if ir is not None:
        out["readiness"] = {
            "evaluator_named": ir.evaluator_named,
            "unknown_ratio": ir.unknown_ratio,
            "diagnosis_confident": ir.diagnosis_confident,
            "binds_writes": ir.binds_writes,
        }
    return out


def _headline(result: Result) -> str:
    o = result.outcome
    if o is Outcome.BASELINE_RECOMMENDED:
        return "A single grounded agent is enough — this is a retrieval problem, not a reasoning one."
    if o is Outcome.BASELINE_FALLBACK:
        return ("Low confidence: the document did not support a design. Falling back to the "
                "grounded baseline as a safe default, NOT as a diagnosed fit.")
    if o is Outcome.PRIMITIVE_SCAFFOLD:
        return ("No catalogue pattern fits cleanly. Here is an UNVERIFIED scaffold composed "
                "from primitives, with the loops and evaluators you must complete.")
    return "Three ways to build this, on a fixed Minimal / Balanced / Ambitious axis, plus the baseline."


def diagnosis_to_dict(ir, cat: Catalogue, questions: list[dict]) -> dict:
    return {
        "diagnosis": [
            {"signature": s.signature_id, "problem": s.problem,
             "pattern": s.pattern, "prior_score": round(s.prior_score, 2),
             "matched_terms": s.matched_terms}
            for s in ir.signatures if s.prior_score > 0
        ],
        "readiness": {
            "evaluator_named": ir.evaluator_named,
            "unknown_ratio": ir.unknown_ratio,
            "diagnosis_confident": ir.diagnosis_confident,
            "reasoning_problem_found": bool(ir.reasoning_signatures),
            "binds_writes": ir.binds_writes,
        },
        "clarifying_questions": questions,
        "next_step": ("Ask the architect the clarifying questions, then call "
                      "recommend_patterns with their answers."),
    }


def pattern_to_dict(p) -> dict:
    return {
        "id": p.id, "title": p.title, "summary": p.summary.strip(),
        "role": p.role,
        "beats_baseline_when": p.beats_baseline_when,
        "answers_signatures": p.problem_signatures,
        "primitives": p.primitives,
        "accepts": p.accepts, "produces": p.produces,
        "evaluator": p.evaluator,
        "budget": {"llm_calls": p.llm_calls, "tokens": p.tokens,
                   "wall_clock_s": p.wall_clock_s},
        "composes_with": p.composes_with,
        "failure_modes": p.failure_modes,
        "agents": p.agents, "skills": p.skills,
        "azure_services": p.azure_services,
    }
