"""The MCP tool surface.

Five tools. The agent's LLM runs the conversation; these do the deterministic
work. Descriptions are written for tool selection — Copilot Studio picks a tool
by its description, so each says plainly WHEN to use it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from patcomp import answers as answers_mod
from patcomp import catalogue as cat_mod
from patcomp import diagnose, intake
from patcomp.legality import kill
from patcomp.models import Candidate, Node
from patcomp.tools_parse import parse_composition as _shared_parse
from patcomp.pipeline import compile_requirements

from . import serialize

_CAT = None


def catalogue():
    global _CAT
    if _CAT is None:
        _CAT = cat_mod.default()
    return _CAT


def set_catalogue(path: str) -> None:
    global _CAT
    _CAT = cat_mod.load(path)


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], dict]


# -------------------------------------------------------------------- handlers
def _diagnose(args: dict) -> dict:
    text = (args.get("requirements") or "").strip()
    if not text:
        raise ValueError("requirements text is required")
    cat = catalogue()
    ir = diagnose.diagnose(intake.parse(text, cat), cat)
    qs = answers_mod.clarifying_questions(ir, cat)
    result = serialize.diagnosis_to_dict(ir, cat, qs)
    result["intake_notes"] = intake.evaluate_ir(ir)
    return result


def _recommend(args: dict) -> dict:
    text = (args.get("requirements") or "").strip()
    if not text:
        raise ValueError("requirements text is required")
    answers = args.get("answers") or None
    cat = catalogue()
    result, _trace = compile_requirements(text, cat, answers=answers)
    return serialize.result_to_dict(result, cat)


def _explain(args: dict) -> dict:
    pid = str(args.get("pattern_id", "")).strip()
    cat = catalogue()
    if pid not in cat.patterns:
        raise ValueError(f"unknown pattern '{pid}'. Valid ids: {sorted(cat.patterns)}")
    return serialize.pattern_to_dict(cat.pattern(pid))


def _list_catalogue(_args: dict) -> dict:
    cat = catalogue()
    return {
        "operators_version": cat.version,
        "counts": {"patterns": len(cat.patterns), "signatures": len(cat.signatures),
                   "rules": len(cat.rules), "contract_types": len(cat.types)},
        "patterns": [
            {"id": p.id, "title": p.title, "role": p.role,
             "answers": p.problem_signatures, "cost_class": p.cost_class,
             "beats_baseline_when": p.beats_baseline_when}
            for p in sorted(cat.patterns.values(), key=lambda x: x.id)
        ],
        "integrity": cat.validate() or "clean",
    }


def _parse_composition(expr: str, cat=None) -> Node:
    return _shared_parse(expr, cat)


def _validate(args: dict) -> dict:
    expr = (args.get("composition") or "").strip()
    if not expr:
        raise ValueError("composition is required, e.g. 'guard(sequence(10,05),04)'")
    cat = catalogue()
    from patcomp.models import IR, EvaluatorCandidate, TaskClass, Field_, ToolBinding
    ir = IR(task_classes=[TaskClass("primary", "decision")])
    ir.objective["outcome"] = Field_.sourced("x", "x")
    ir.evaluator_candidates = [EvaluatorCandidate("primary", "declared", "hybrid")]
    if args.get("binds_writes"):
        ir.tools = [ToolBinding("system-of-record", "write")]
    node = _parse_composition(expr, cat)
    cand = kill(Candidate(node), ir, cat)
    return {
        "composition": node.signature(),
        "legal": cand.alive,
        "verdict": "legal" if cand.alive else "illegal",
        "violations": [{"rule": k.rule_id, "reason": k.reason.strip(),
                        "repair": (k.repair or "").strip()} for k in cand.kills],
        "warnings": [{"rule": k.rule_id, "reason": k.reason.strip()} for k in cand.warnings],
    }


def _diagram(args: dict) -> dict:
    from patcomp import diagram
    cat = catalogue()
    pid = str(args.get("pattern_id", "")).strip()
    comp = str(args.get("composition", "")).strip()
    if pid:
        if pid not in cat.patterns:
            raise ValueError(f"unknown pattern '{pid}'")
        return {"kind": "pattern", "pattern_id": pid,
                "mermaid": diagram.pattern_mermaid(pid),
                "markdown": diagram.pattern_markdown(pid, cat)}
    if comp:
        node = _shared_parse(comp, cat)
        return {"kind": "composition", "composition": node.signature(),
                "mermaid": diagram.composition_mermaid(node, cat),
                "markdown": diagram.composition_markdown(node, cat)}
    raise ValueError("provide pattern_id or composition")


def _emit(args: dict) -> dict:
    """Return the emitted project as a file manifest plus inline contents.

    A binary zip cannot travel over MCP JSON, so this returns the file tree and
    the text of every file; the caller (or a thin service around it) writes the
    zip. Use scope='all' for the whole catalogue, or pass requirements for a
    solution project."""
    from patcomp import emit
    cat = catalogue()
    scope = args.get("scope", "solution")
    if scope == "all":
        files = emit.emit_catalogue_project(cat)
        label = "catalogue"
    else:
        text = (args.get("requirements") or "").strip()
        if not text:
            raise ValueError("requirements text is required for a solution project")
        result, _ = compile_requirements(text, cat, answers=args.get("answers") or None)
        files = emit.emit_solution_project(result, cat, args.get("name", "solution"))
        label = "solution"
    manifest = sorted(files.keys())
    # A full-catalogue emit is ~130 files / ~90 KB; do not inline that into a
    # single tool response unless explicitly asked. A solution emit is small,
    # so inline it by default. The caller writes the .zip.
    default_inline = scope != "all"
    inline = bool(args.get("include_contents", default_inline))
    out = {"scope": label, "file_count": len(files), "files": manifest,
           "verify": "python verify_structure.py", "verification": "structural",
           "contents_inlined": inline}
    if inline:
        out["contents"] = files
    else:
        out["note"] = ("File contents omitted for size (scope='all'). Re-call with "
                       "include_contents=true to inline, or emit the .zip service-side.")
    return out


# -------------------------------------------------------------------- registry
TOOLS: list[Tool] = [
    Tool(
        name="diagnose_requirements",
        description=(
            "Diagnose a requirements scenario against the reasoning-pattern selection "
            "matrix and return which clarifying questions to ask before recommending. "
            "Call this FIRST when the architect describes a scenario. Returns the "
            "detected problem signatures, readiness flags, and a short list of "
            "clarifying_questions. Ask the user those questions, then call "
            "recommend_patterns with their answers."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "requirements": {"type": "string",
                                 "description": "The architect's scenario or requirements text."}
            },
            "required": ["requirements"],
        },
        handler=_diagnose,
    ),
    Tool(
        name="recommend_patterns",
        description=(
            "Recommend an agentic reasoning architecture for a scenario. Returns ONE of: "
            "three ranked options (Minimal / Balanced / Ambitious) plus a baseline; a "
            "grounded-baseline recommendation when it is purely a retrieval problem; an "
            "UNVERIFIED primitive scaffold when no catalogue pattern fits; or a "
            "low-confidence baseline fallback when the document cannot support a design. "
            "Pass the architect's answers (from diagnose_requirements) in 'answers' for a "
            "confident result. Cost and latency figures are computed, never invented; "
            "unverified scaffolds carry no cost figure by design."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "requirements": {"type": "string",
                                 "description": "The architect's scenario or requirements text."},
                "answers": {
                    "type": "object",
                    "description": "Optional architect answers gathered conversationally.",
                    "properties": {
                        "problem_confirmed": {"type": "boolean"},
                        "reasoning_family": {"type": "string",
                                             "description": "A signature id to force, e.g. 'multiple_interpretations'."},
                        "evaluator": {"type": "string",
                                      "enum": ["test", "rule", "model", "human", "none"]},
                        "control": {"type": "string",
                                    "enum": ["approve", "deterministic", "both", "none"]},
                        "acts_on_systems": {"type": "boolean"},
                        "success_stated": {"type": "boolean"},
                        "sensitive_data": {"type": "boolean"},
                    },
                },
            },
            "required": ["requirements"],
        },
        handler=_recommend,
    ),
    Tool(
        name="explain_pattern",
        description=(
            "Explain one catalogue pattern by id (00-13): what it is, when it beats a "
            "grounded baseline, what it accepts and produces, its budget, and its known "
            "failure modes. Use when the architect asks what a pattern in a recommendation means."
        ),
        input_schema={
            "type": "object",
            "properties": {"pattern_id": {"type": "string",
                                          "description": "Pattern id, e.g. '05'."}},
            "required": ["pattern_id"],
        },
        handler=_explain,
    ),
    Tool(
        name="list_catalogue",
        description=(
            "List every reasoning pattern in the catalogue with its role, the problems it "
            "answers, and when it beats a baseline. Use to browse options or when the "
            "architect asks what patterns exist."
        ),
        input_schema={"type": "object", "properties": {}},
        handler=_list_catalogue,
    ),
    Tool(
        name="get_pattern_diagram",
        description=(
            "Return a Mermaid flowchart (and markdown) for a pattern or a composition, "
            "in the style of the reasoning field guide. Pass 'pattern_id' (00-13) for one "
            "pattern, or 'composition' like 'guard(sequence(10,05),04)' for a full "
            "recommendation. Use to show the architect a diagram of what is recommended."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern_id": {"type": "string", "description": "e.g. '05'"},
                "composition": {"type": "string", "description": "e.g. 'guard(sequence(10,05),04)'"},
            },
        },
        handler=_diagram,
    ),
    Tool(
        name="emit_foundry_project",
        description=(
            "Emit a customisable Azure AI Foundry project scaffold using Microsoft "
            "libraries (Agent Framework / MAF, Azure AI Agent Service, azure-ai-evaluation). "
            "scope='solution' with a requirements text emits a project for the recommended "
            "composition; scope='all' emits the whole pattern catalogue. Returns the file "
            "tree and file contents (a thin service writes the .zip). Every pattern ships an "
            "evaluator; tool bindings default read-only."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "scope": {"type": "string", "enum": ["solution", "all"]},
                "requirements": {"type": "string"},
                "answers": {"type": "object"},
                "name": {"type": "string"},
                "include_contents": {"type": "boolean",
                                     "description": "Return file contents inline (default true)."},
            },
        },
        handler=_emit,
    ),
    Tool(
        name="validate_composition",
        description=(
            "Check whether a hand-proposed composition is legal against the deterministic "
            "rules — contract joins, governed writes, bounded loops, known conflicts. Input "
            "a composition expression like 'guard(sequence(10,05),04)'. Returns legal/illegal "
            "with the specific rule violated and how to repair it. Use when an architect "
            "proposes their own design and asks if it holds together."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "composition": {"type": "string",
                                "description": "e.g. 'guard(sequence(10,05),04)' or a bare id like '02'."},
                "binds_writes": {"type": "boolean",
                                 "description": "True if the composition binds write-capable tools."},
            },
            "required": ["composition"],
        },
        handler=_validate,
    ),
]

BY_NAME = {t.name: t for t in TOOLS}
