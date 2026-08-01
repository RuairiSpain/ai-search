"""Signature confusability audit — dev-time tooling, not part of the runtime
pipeline.

Diagnosis is deliberately multi-label (§2: "real projects match three or
four rows of the matrix"), so *some* overlap between signatures is expected
and fine. What this catches is the accidental kind: an evidence term added to
help signature A starts reading as support for unrelated signature B. Two
real examples shipped before being caught by eye ("context across" matching
cross_session_recall against an unrelated relationship-discovery scenario,
and a bare "history" term matching case-history language) — this makes that
check repeatable instead of relying on someone noticing.

Run via `patcomp audit-signatures`, or import `audit()` directly. Intended to
be run by a human (or CI) after any edit to signatures.yaml, not on every
diagnosis call — it is not wired into diagnose.py at all.
"""
from __future__ import annotations

from . import diagnose
from .catalogue import Catalogue, default as default_catalogue

# Signature pairs known to share vocabulary territory or sit structurally
# adjacent in the §2 selection matrix — worth a specific check whenever
# either side's evidence list changes. Not exhaustive: add a pair here the
# first time an unintended overlap between it and another signature is found,
# the same way test cases get added after a miss, not speculatively upfront.
CONFUSABLE_PAIRS: list[tuple[str, str]] = [
    ("weak_judgement", "multiple_interpretations"),
    ("cross_session_recall", "long_running_process"),
    ("needs_tools_midreasoning", "planning_under_constraints"),
    ("needs_tools_midreasoning", "relationship_discovery"),
    ("deterministic_policy_compliance", "human_judgement_in_output"),
    ("workflow_too_large", "planning_under_constraints"),
    ("workflow_too_large", "needs_tools_midreasoning"),
    ("should_improve_over_runs", "weak_judgement"),
    ("validated_artefacts", "deterministic_policy_compliance"),
]


def _probe_text(cat: Catalogue, signature_id: str) -> str:
    """A short corpus representing what a signature actually means, built
    from catalogue content only — never invented text — so the audit stays
    grounded in the same source the evidence terms are supposed to encode."""
    sig = cat.signature(signature_id)
    if sig is None:
        raise KeyError(f"unknown signature '{signature_id}'")
    parts = [sig.problem]
    if sig.pattern:
        p = cat.pattern(sig.pattern)
        parts += [p.summary, p.beats_baseline_when]
    return " ".join(part for part in parts if part)


def check_pair(cat: Catalogue, sig_a: str, sig_b: str) -> dict[str, list[str]]:
    """Does either signature's evidence list read as support for the other's
    own problem description? Returns the offending terms on each side."""
    a = cat.signature(sig_a)
    b = cat.signature(sig_b)
    text_b = diagnose.normalise(_probe_text(cat, sig_b))
    text_a = diagnose.normalise(_probe_text(cat, sig_a))
    a_terms_that_hit_b = [t for t in a.evidence if diagnose.term_weight(text_b, t) > 0]
    b_terms_that_hit_a = [t for t in b.evidence if diagnose.term_weight(text_a, t) > 0]
    return {"a_terms_that_hit_b": a_terms_that_hit_b,
           "b_terms_that_hit_a": b_terms_that_hit_a}


def audit(cat: Catalogue | None = None,
         pairs: list[tuple[str, str]] | None = None) -> dict[tuple[str, str], dict[str, list[str]]]:
    """Runs check_pair over every registered pair. Returns only pairs with at
    least one collision — an empty dict means the registered pairs are clean."""
    cat = cat or default_catalogue()
    pairs = CONFUSABLE_PAIRS if pairs is None else pairs
    out: dict[tuple[str, str], dict[str, list[str]]] = {}
    for a, b in pairs:
        result = check_pair(cat, a, b)
        if result["a_terms_that_hit_b"] or result["b_terms_that_hit_a"]:
            out[(a, b)] = result
    return out


def report(cat: Catalogue | None = None) -> str:
    cat = cat or default_catalogue()
    findings = audit(cat)
    if not findings:
        return (f"Signature confusability audit — {len(CONFUSABLE_PAIRS)} pairs checked, "
                "0 collisions. Clean.")
    out = [f"Signature confusability audit — {len(CONFUSABLE_PAIRS)} pairs checked, "
          f"{len(findings)} with a collision:"]
    for (a, b), result in findings.items():
        out.append(f"  {a}  <->  {b}")
        for t in result["a_terms_that_hit_b"]:
            out.append(f"    {a}.evidence '{t}' also reads as {b}")
        for t in result["b_terms_that_hit_a"]:
            out.append(f"    {b}.evidence '{t}' also reads as {a}")
    return "\n".join(out)
