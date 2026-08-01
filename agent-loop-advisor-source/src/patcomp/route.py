"""Outcome routing — the ladder.

Two axes, deliberately separated:
  TIER       is the emitted ARTEFACT verified?   (1 catalogue | 2 scaffold)
  CONFIDENCE do we trust it is the RIGHT design? (high | medium | low)

The grounded baseline is emitted at HIGH confidence when diagnosis positively
finds a retrieval problem, and at LOW confidence when we could not diagnose at
all. Same artefact, different claim. descent_reason is what keeps them apart.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .models import (IR, Candidate, Confidence, Kill, Outcome, Result)
from . import generate, legality, estimate, primitives

# Calibrated 2026-08-01 against golden-set.yaml. The measured blast-weighted
# unknown_ratio across all 19 cases spans 0.133 to 0.383, so both thresholds
# sit above the observed range and act as a SAFETY NET for documents worse than
# anything in the set. The ladder is driven in practice by diagnosis_confident
# and evaluator_named, which do discriminate (2/2 on the insufficient-input
# cases). Stated plainly so a threshold that never fires is not mistaken for
# one that is doing work.
T_UNKNOWN_TIER1 = 0.45
T_UNKNOWN_FALLBACK = 0.60

# §21's discovery questions, used when we cannot design.
QUESTIONS = {
    "failure_locus": "Where does the current process fail — finding information, or deciding what to do with it?",
    "quality": "What would \"good\" look like, and how would we measure it?",
    "control": "Which decisions must be deterministic, which actions need approval, and where do humans participate in the reasoning itself?",
    "envelopes": "What are the cost and latency envelopes per task class?",
    "family": "Is the failure in judgement, action, memory, process, compliance or relationships?",
    "volume": "Is any workload stable and high-volume enough to move reasoning into the model?",
}


def _questions_for(reason: str) -> list[str]:
    if reason == "evaluator_missing":
        return [QUESTIONS["quality"], QUESTIONS["control"], QUESTIONS["envelopes"]]
    if reason == "insufficient_input":
        return [QUESTIONS["failure_locus"], QUESTIONS["quality"],
                QUESTIONS["control"], QUESTIONS["envelopes"]]
    if reason == "no_catalogue_fit":
        return [QUESTIONS["family"], QUESTIONS["quality"], QUESTIONS["control"]]
    return [QUESTIONS["failure_locus"], QUESTIONS["quality"], QUESTIONS["control"]]


def _fallback(ir: IR, cat: Catalogue, reason: str,
              log: list[tuple[str, Kill]]) -> Result:
    base = estimate.estimate(generate.baseline(cat), ir, cat)
    return Result(
        outcome=Outcome.BASELINE_FALLBACK, tier=1, verified=True,
        confidence=Confidence.LOW, descent_reason=reason,
        baseline=base, questions=_questions_for(reason), kill_log=log, ir=ir,
    )


def route(ir: IR, cat: Catalogue) -> Result:
    log: list[tuple[str, Kill]] = []

    # --- lowest rung first: can we design at all?
    if not ir.diagnosis_confident:
        return _fallback(ir, cat, "low_confidence", log)
    if ir.unknown_ratio > T_UNKNOWN_FALLBACK:
        return _fallback(ir, cat, "insufficient_input", log)

    base = estimate.estimate(generate.baseline(cat), ir, cat)

    # --- no reasoning failure: grounding is the answer, and that is a finding
    if not ir.reasoning_signatures:
        return Result(
            outcome=Outcome.BASELINE_RECOMMENDED, tier=1, verified=True,
            confidence=Confidence.HIGH, baseline=base, kill_log=log, ir=ir,
        )

    # --- generate, then kill for free
    cands = generate.candidates(ir, cat)
    survivors, log = legality.kill_all(cands, ir, cat)

    if not ir.evaluator_named:
        scaffold = primitives.build(ir, cat, ["no evaluator named — you must define \"good\""])
        return Result(
            outcome=Outcome.PRIMITIVE_SCAFFOLD, tier=2, verified=False,
            confidence=Confidence.MEDIUM, descent_reason="evaluator_missing",
            scaffold=scaffold, baseline=base,
            questions=_questions_for("evaluator_missing"), kill_log=log, ir=ir,
        )

    if not survivors:
        reasons = ["no catalogue composition survived the legality rules"]
        reasons += sorted({k.rule_id for _s, k in log})[:3]
        scaffold = primitives.build(ir, cat, reasons)
        return Result(
            outcome=Outcome.PRIMITIVE_SCAFFOLD, tier=2, verified=False,
            confidence=Confidence.MEDIUM, descent_reason="no_catalogue_fit",
            scaffold=scaffold, baseline=base,
            questions=_questions_for("no_catalogue_fit"), kill_log=log, ir=ir,
        )

    if ir.unknown_ratio > T_UNKNOWN_TIER1:
        scaffold = primitives.build(
            ir, cat, [f"{int(ir.unknown_ratio*100)}% of requirement fields were assumed"])
        return Result(
            outcome=Outcome.PRIMITIVE_SCAFFOLD, tier=2, verified=False,
            confidence=Confidence.MEDIUM, descent_reason="insufficient_input",
            scaffold=scaffold, baseline=base,
            questions=_questions_for("insufficient_input"), kill_log=log, ir=ir,
        )

    survivors = estimate.estimate_all(survivors, ir, cat)
    chosen = select_three(survivors, cat, ir)
    return Result(
        outcome=Outcome.THREE_CARDS, tier=1, verified=True,
        confidence=Confidence.HIGH, candidates=chosen, baseline=base,
        kill_log=log, ir=ir,
    )


def select_three(survivors: list[Candidate], cat: Catalogue,
                 ir=None) -> list[Candidate]:
    """Pick three distinct candidates on the fixed Minimal/Balanced/Ambitious
    axis (learnable, ordered by machinery/cost), and flag the RECOMMENDED one.

    The recommendation is the best-fit candidate — the one covering the most
    diagnosed reasoning patterns, tie-broken by lower cost — NOT merely the
    median-cost option. distinct_candidates: never present two candidates that
    share a composition signature."""
    by_axis: dict[str, list[Candidate]] = {}
    for c in survivors:
        by_axis.setdefault(c.axis, []).append(c)
    for lst in by_axis.values():
        lst.sort(key=lambda c: (c.cost_per_task or 0, c.tree.depth()))

    # Pick the best survivor from each generation-INTENT bucket, distinct.
    picks: dict[str, Candidate] = {}
    seen: set[str] = set()
    for axis in (generate.MINIMAL, generate.BALANCED, generate.AMBITIOUS):
        for c in by_axis.get(axis, []):
            if c.signature not in seen:
                picks[axis] = c
                seen.add(c.signature)
                break

    out = list(picks.values())
    # Backfill to three distinct candidates from anything left over.
    if len(out) < 3:
        for c in sorted(survivors, key=lambda c: (c.cost_per_task or 0)):
            if len(out) >= 3:
                break
            if c.signature not in seen:
                out.append(c)
                seen.add(c.signature)

    # The RECOMMENDATION is the BALANCED-intent pick — the composed, governed
    # best-fit that generation produced for that role — NOT whichever card
    # happens to land in the middle by price. Capture it BEFORE relabelling.
    out = sorted(out, key=lambda c: (c.cost_per_task or 0, c.tree.depth()))[:3]
    recommended = picks.get(generate.BALANCED)
    if recommended is None or not any(c is recommended for c in out):
        recommended = out[len(out) // 2] if out else None

    # The axis is a learnable MACHINERY ladder ordered by cost/depth: Minimal
    # (least) → Balanced → Ambitious (most). The recommendation is flagged
    # explicitly and is usually Balanced, but honestly may be Minimal (grounding
    # nearly suffices) or Ambitious (the problem genuinely needs the most) — a
    # star that always sat on the middle card would be decoration, not a signal.
    labels = [generate.MINIMAL, generate.BALANCED, generate.AMBITIOUS]
    for i, c in enumerate(out):
        c.axis = labels[i] if i < len(labels) else generate.AMBITIOUS
        c.recommended = c is recommended
    return out
