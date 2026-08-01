"""Structured interview answers.

The CLI runs the interview as a callback loop. An agent runtime (Copilot Studio)
instead gathers the same answers conversationally and passes them as a dict.
This module maps that dict onto IR mutations, and surfaces the clarifying
questions the agent should ask — so the two front ends share one reasoning core.

The answer fields are deliberately few and stable, so an LLM can fill them from
natural conversation without knowing anything about the catalogue internals.
"""
from __future__ import annotations

from typing import Any

from .catalogue import Catalogue
from .models import IR, EvaluatorCandidate, Field_, ToolBinding

_EVAL_KIND = {
    "test": "test_based", "rule": "rule_based",
    "model": "model_judge", "human": "human",
}


def apply_answers(ir: IR, answers: dict[str, Any] | None, cat: Catalogue) -> IR:
    """Apply an architect's structured answers. Every field is optional; a
    missing field leaves the extracted IR untouched. The architect's answer
    always wins over the document prior."""
    a = answers or {}
    tc = ir.task_classes[0].id if ir.task_classes else "primary"

    # --- how would you know an answer was good? (the one hard gate)
    ev = a.get("evaluator")
    if ev in _EVAL_KIND:
        ir.evaluator_candidates = [
            EvaluatorCandidate(tc, "declared by the architect", _EVAL_KIND[ev])]
    elif ev == "none":
        ir.evaluator_candidates = []

    # --- does it write to a real system?
    acts = a.get("acts_on_systems")
    if acts is False:
        ir.tools = [t for t in ir.tools if not t.is_write]
    elif acts is True and not ir.binds_writes:
        ir.tools.append(ToolBinding("system-of-record", "write"))

    # --- the control boundary
    ctl = a.get("control")
    if ctl == "approve":
        ir.needs_approval, ir.must_be_deterministic = [tc], []
    elif ctl == "deterministic":
        ir.must_be_deterministic = [tc]
    elif ctl == "both":
        ir.needs_approval, ir.must_be_deterministic = [tc], [tc]
    elif ctl == "none":
        ir.needs_approval, ir.must_be_deterministic = [], []

    # --- can you state what success looks like?
    if a.get("success_stated") is True:
        ir.objective["success_criteria"] = Field_.from_user("stated by the architect")

    # --- confirm or correct the diagnosis
    fam = a.get("reasoning_family")
    if fam:
        for s in ir.signatures:
            if s.signature_id == fam:
                s.user_label = True
    confirmed = a.get("problem_confirmed")
    if confirmed is True:
        top = _top_signature(ir)
        if top:
            top.user_label = True
    elif confirmed is False:
        top = _top_signature(ir)
        if top:
            top.user_label = False

    if a.get("sensitive_data") is not None:
        ir.sensitive_data = bool(a["sensitive_data"])
    return ir


def _top_signature(ir: IR):
    ranked = [s for s in ir.signatures if s.pattern and s.prior_score > 0]
    return max(ranked, key=lambda s: s.prior_score, default=None)


# Stable option -> answer-value maps, so the agent can present readable choices.
CLARIFYING = {
    "evaluator": {
        "prompt": "How would you know an answer was good?",
        "field": "evaluator",
        "options": [
            ("A test suite can check it", "test"),
            ("A rule can check it", "rule"),
            ("Another model can judge it", "model"),
            ("Only a human can judge it", "human"),
            ("We cannot say yet", "none"),
        ],
        "why": "No evaluator, no verified build. This is the one hard gate.",
    },
    "control": {
        "prompt": "What has to be true before the system acts?",
        "field": "control",
        "options": [
            ("A human approves first", "approve"),
            ("A rule must decide, every time", "deterministic"),
            ("Both", "both"),
            ("Neither — it can act on its own", "none"),
        ],
        "why": "Draws the control boundary between deterministic and approved actions.",
    },
    "writes": {
        "prompt": "Will it write to a real system of record, or only read and recommend?",
        "field": "acts_on_systems",
        "options": [
            ("It takes actions / writes", True),
            ("It only reads and recommends", False),
        ],
        "why": "A write boundary must be guarded or gated, or the build fails.",
    },
    "success": {
        "prompt": "Can you state, in one sentence, what a successful outcome looks like?",
        "field": "success_stated",
        "options": [("Yes", True), ("Not yet", False)],
        "why": "Without it, the recommendation rests on our assumptions, not yours.",
    },
    "confirm": {
        "prompt": "Does this describe your problem?",   # agent fills the diagnosis text
        "field": "problem_confirmed",
        "options": [("Yes", True), ("No", False)],
        "why": "Confirms which pattern family answers your problem.",
    },
}


def clarifying_questions(ir: IR, cat: Catalogue) -> list[dict]:
    """The subset of questions that would change the recommendation. The agent
    asks these conversationally, then calls recommend with the answers."""
    qs: list[dict] = []

    top = _top_signature(ir)
    if top:
        q = dict(CLARIFYING["confirm"])
        q["prompt"] = f'Does this describe your problem: "{top.problem}"?'
        qs.append(q)

    if not ir.evaluator_named:
        qs.append(CLARIFYING["evaluator"])

    # control only matters if not already clearly determined by the document
    if not (ir.needs_approval or ir.must_be_deterministic):
        qs.append(CLARIFYING["control"])

    if ir.binds_writes:
        qs.append(CLARIFYING["writes"])

    sc = ir.objective.get("success_criteria")
    if sc is not None and sc.provenance.is_assumed:
        qs.append(CLARIFYING["success"])

    # normalise options to plain dicts for JSON
    out = []
    for q in qs[:5]:
        out.append({
            "id": q["field"],
            "prompt": q["prompt"],
            "why": q["why"],
            "options": [{"label": lbl, "value": val} for lbl, val in q["options"]],
        })
    return out
