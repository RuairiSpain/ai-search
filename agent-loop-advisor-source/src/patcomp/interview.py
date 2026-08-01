"""Stage 3 — the architect interview.

This is the assumption gate and the diagnosis confirmation in one pass. It is
the ONLY question step: three to five items, defaults pre-selected, each with
one line on why it matters. Only questions that change the recommendation are
asked; everything else is applied silently and logged.

The interview beats the document prior outright. A human architect answering
"yes, the first explanation is usually wrong" is better evidence than any
keyword match, which is why the prior is a hint and never a verdict.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from .catalogue import Catalogue
from .models import IR, Blast, EvaluatorCandidate, Field_, ToolBinding

Asker = Callable[[str, list[str], int], int]


@dataclass
class Question:
    key: str
    prompt: str
    options: list[str]
    why: str
    default: int = 0
    apply: Callable[[IR, int], None] = field(default=lambda ir, i: None)


def _confirm_signature_questions(ir: IR, cat: Catalogue) -> list[Question]:
    """Confirm the top prior labels and offer the strongest near-misses.

    §21's second question, made concrete: is the failure in judgement, action,
    memory, process, compliance or relationships?
    """
    qs: list[Question] = []
    ranked = sorted(
        [s for s in ir.signatures if s.pattern],
        key=lambda s: s.prior_score, reverse=True)

    top = [s for s in ranked if s.prior_score > 0][:3]
    for sig in top:
        def _mk(sig_id: str):
            def apply(ir: IR, choice: int):
                for s in ir.signatures:
                    if s.signature_id == sig_id:
                        s.user_label = (choice == 0)
            return apply
        qs.append(Question(
            key=f"sig:{sig.signature_id}",
            prompt=f"Does this describe your problem?  \"{sig.problem}\"",
            options=["Yes", "No"],
            why="This selects which pattern family answers your problem.",
            default=0,
            apply=_mk(sig.signature_id),
        ))

    if not top:
        # Nothing matched. Ask the §21 family question directly.
        choices = [s for s in ranked][:6]
        def apply(ir: IR, choice: int):
            if choice < len(choices):
                for s in ir.signatures:
                    if s.signature_id == choices[choice].signature_id:
                        s.user_label = True
        qs.append(Question(
            key="family",
            prompt="Where does the current process fail?",
            options=[s.problem for s in choices] + ["None of these — it is a retrieval problem"],
            why="Separates reasoning work from retrieval work.",
            default=len(choices),
            apply=apply,
        ))
    return qs


def _evaluator_question(ir: IR) -> Question | None:
    if ir.evaluator_named:
        return None

    def apply(ir: IR, choice: int):
        kinds = ["test_based", "rule_based", "model_judge", "human"]
        if choice < len(kinds):
            tc = ir.task_classes[0].id if ir.task_classes else "primary"
            ir.evaluator_candidates = [EvaluatorCandidate(
                tc, "declared during the interview", kinds[choice])]
        # choice == len(kinds) -> cannot say. Leave unnamed; the ladder descends.

    return Question(
        key="evaluator",
        prompt="How would you know an answer was good?",
        options=["A test suite can check it", "A rule can check it",
                 "Another model can judge it", "Only a human can judge it",
                 "We cannot say yet"],
        why="No evaluator, no verified build. This is the one hard gate.",
        default=2,
        apply=apply,
    )


def _control_question(ir: IR) -> Question:
    def apply(ir: IR, choice: int):
        tc = ir.task_classes[0].id if ir.task_classes else "primary"
        ir.needs_approval = [tc] if choice in (0, 2) else []
        ir.must_be_deterministic = [tc] if choice in (1, 2) else []

    default = 0 if ir.needs_approval else (1 if ir.must_be_deterministic else 3)
    return Question(
        key="control",
        prompt="What has to be true before the system acts?",
        options=["A human approves first",
                 "A rule must decide, every time",
                 "Both",
                 "Neither — it can act on its own"],
        why="Draws the control boundary: which decisions are deterministic and which need approval.",
        default=default,
        apply=apply,
    )


def _writes_question(ir: IR) -> Question | None:
    if not ir.binds_writes:
        return None

    def apply(ir: IR, choice: int):
        if choice == 1:
            ir.tools = [t for t in ir.tools if not t.is_write]

    return Question(
        key="writes",
        prompt="Will the system write to a real system of record?",
        options=["Yes — it takes actions", "No — it only reads and recommends"],
        why="A write boundary must be guarded or gated, or the build fails.",
        default=0,
        apply=apply,
    )


def _objective_question(ir: IR) -> Question | None:
    f = ir.objective.get("success_criteria")
    if f is not None and not f.provenance.is_assumed:
        return None

    def apply(ir: IR, choice: int):
        if choice == 0:
            ir.objective["success_criteria"] = Field_.from_user("stated during interview")

    return Question(
        key="success",
        prompt="Can you state, in one sentence, what a successful outcome looks like?",
        options=["Yes", "Not yet"],
        why="Without it, any recommendation rests on our assumptions rather than yours.",
        default=0,
        apply=apply,
    )


def build(ir: IR, cat: Catalogue, limit: int = 5) -> list[Question]:
    qs: list[Question] = []
    qs.extend(_confirm_signature_questions(ir, cat))
    for q in (_evaluator_question(ir), _control_question(ir),
              _writes_question(ir), _objective_question(ir)):
        if q is not None:
            qs.append(q)
    return qs[:limit]


def run(ir: IR, cat: Catalogue, ask: Asker, limit: int = 5) -> IR:
    """Ask, then apply. `ask` returns the chosen option index."""
    for q in build(ir, cat, limit):
        choice = ask(q.prompt, q.options, q.default)
        if choice is None or choice < 0 or choice >= len(q.options):
            choice = q.default
        q.apply(ir, choice)
    return ir
