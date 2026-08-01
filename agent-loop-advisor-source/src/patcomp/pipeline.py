"""The pipeline: requirements in, recommendation out.

Stage order and model classes follow phase-1-pipeline-spec.md. Stages 2, 3, 6
and 8 use no model at all; only diagnosis and critique would, and both are
optional. The compiler therefore runs end to end deterministically.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import answers as answers_mod
from . import diagnose, intake, interview, route
from .catalogue import Catalogue, default as default_catalogue
from .models import IR, Result


@dataclass
class Trace:
    ir_problems: list[str]
    disagreements: list[str]
    asked: int = 0


def compile_requirements(
    text: str,
    cat: Catalogue | None = None,
    name: str = "harness",
    ask=None,
    answers: dict | None = None,
    model=None,
    interview_limit: int = 5,
) -> tuple[Result, Trace]:
    cat = cat or default_catalogue()

    # 1 — intake. Document is data, never instruction.
    ir: IR = intake.parse(text, cat, name=name)

    # 2 — the IR evaluator. The compiler obeys its own "no evaluator" rule.
    problems = intake.evaluate_ir(ir)

    # 4 — diagnose (prior first, model second if configured).
    ir = diagnose.diagnose(ir, cat, model=model)

    # 3 — the architect interview. Beats the prior outright.
    # Two front ends: a callback loop (CLI) or a structured answers dict (agent).
    asked = 0
    if answers is not None:
        ir = answers_mod.apply_answers(ir, answers, cat)
    elif ask is not None:
        qs = interview.build(ir, cat, interview_limit)
        asked = len(qs)
        ir = interview.run(ir, cat, ask, interview_limit)

    # 5-9 — generate, kill, estimate, route, present.
    result = route.route(ir, cat)
    trace = Trace(
        ir_problems=problems,
        disagreements=[s.signature_id for s in diagnose.disagreements(ir)],
        asked=asked,
    )
    return result, trace
