"""Stage 9 — presentation.

Fixed axes, six facts in a fixed order, plain language on the face and pattern
names one expand away. Users who run the tool twice learn the axis once.
"""
from __future__ import annotations

from .catalogue import Catalogue
from .estimate import band_note
from .models import Candidate, Confidence, IR, Outcome, Result

CHIP = {
    "00": "grounded answer", "01": "deliberate reasoning", "02": "tool loop",
    "03": "planner + workers", "04": "rules guard", "05": "branching",
    "06": "memory", "07": "reflection", "08": "workflow state",
    "09": "constrained planning", "10": "graph context", "11": "test-repair",
    "12": "pattern compiler", "13": "human gate",
}


def chips(cand: Candidate, cat: Catalogue) -> str:
    return " → ".join(f"[{CHIP.get(p, p)}]" for p in cand.tree.patterns())


def top_risk(cand: Candidate, cat: Catalogue) -> str:
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        if p.failure_modes:
            return p.failure_modes[0].replace("_", " ")
    return "none catalogued"


def beats_baseline(cand: Candidate, cat: Catalogue) -> str:
    """Every non-baseline card must carry a satisfied beats_baseline_when."""
    for pid in cand.tree.patterns():
        p = cat.pattern(pid)
        if p.role in ("reasoning", "planning", "context", "action",
                      "orchestration", "artefact", "process", "improvement"):
            return p.beats_baseline_when
    return cat.pattern(cand.tree.patterns()[0]).beats_baseline_when


def humans_sit(cand: Candidate, cat: Catalogue) -> str:
    pats = cand.tree.patterns()
    if "13" in pats:
        return "final sign-off before any action is taken"
    if "08" in pats:
        return "named states inside the workflow"
    if "07" in pats:
        return "reviewing skills before they are promoted"
    return "no human in the loop"


def tradeoff(cand: Candidate, others: list[Candidate], cat: Catalogue) -> str:
    if not others or cand.cost_per_task is None:
        return "—"
    cheapest = min((o.cost_per_task or 0) for o in others if o is not cand) \
        if len(others) > 1 else cand.cost_per_task
    parts = []
    if cheapest and cand.cost_per_task > cheapest:
        parts.append(f"costs ~{cand.cost_per_task / max(cheapest, 0.0001):.1f}x the cheapest option")
    pats = cand.tree.patterns()
    if "04" in pats or "13" in pats:
        parts.append("but is the one you can audit")
    if cat.pattern(pats[0]).has_loop:
        parts.append("and adds a loop you must budget and evaluate")
    return "; ".join(parts) if parts else "least machinery, least to govern"


def render(result: Result, cat: Catalogue, width: int = 74) -> str:
    ir = result.ir
    out: list[str] = []
    bar = "─" * width

    def hdr(text: str):
        out.append(bar)
        out.append(text)
        out.append(bar)

    if result.outcome is Outcome.BASELINE_RECOMMENDED:
        hdr("A SINGLE GROUNDED AGENT IS ENOUGH FOR THIS")
        out.append("")
        out.append("What you described is an information-access problem, not a")
        out.append("reasoning problem. It needs to find the right content and cite")
        out.append("it — not compare options, plan, verify or justify.")
        out.append("")
        b = result.baseline
        if b:
            out.append(f"  Recommended : one grounded agent, no orchestration")
            out.append(f"  Cost        : ~EUR{b.cost_per_task:.4f}/task   ~{b.latency_s:.0f}s")
        out.append("")
        out.append("This stops being enough when you start asking it to accept,")
        out.append("reject or escalate — to decide rather than retrieve.")
        return "\n".join(out)

    if result.outcome is Outcome.BASELINE_FALLBACK:
        hdr("!  LOW CONFIDENCE — a safe default, not a diagnosis")
        out.append("")
        out.append("Your requirements did not contain enough for us to design a")
        out.append("reasoning system, so we have not pretended otherwise.")
        out.append("")
        out.append("We have NOT concluded that grounding is right for you. We fell")
        out.append("back to it because it is the safest useful starting point.")
        out.append("")
        b = result.baseline
        if b:
            out.append("  What you get: one agent, grounded on your knowledge source,")
            out.append("                no orchestration. Implemented and tested.")
            out.append(f"  Cost        : ~EUR{b.cost_per_task:.4f}/task   ~{b.latency_s:.0f}s")
        out.append("")
        out.append(f"  Why we could not go further: {result.descent_reason}")
        if ir:
            out.append(f"  {int(ir.unknown_ratio * 100)}% of requirement fields were assumed")
        out.append("")
        out.append("  Read-only. No write access is emitted here, by rule.")
        out.append("")
        out.append("These answers would unlock a real design:")
        for i, q in enumerate(result.questions, 1):
            out.append(f"  {i}. {q}")
        return "\n".join(out)

    if result.outcome is Outcome.PRIMITIVE_SCAFFOLD:
        sc = result.scaffold
        hdr("!  UNVERIFIED SCAFFOLD — not production-ready")
        out.append("")
        out.append((sc.rationale if sc else ""))
        out.append("")
        out.append("  Built from primitives:")
        for p in (sc.primitives if sc else []):
            out.append(f"    - {p}")
        out.append("")
        out.append("  Suggested loops:")
        for l in (sc.loops if sc else []):
            out.append(f"    - {l}")
        out.append("")
        out.append("  Evaluator for each loop (the architecture IS the evaluator):")
        for e in (sc.evaluators if sc else []):
            out.append(f"    - {e}")
        out.append("")
        out.append("  Dependencies you must supply:")
        for d in (sc.dependencies if sc else []):
            out.append(f"    - {d}")
        out.append("")
        out.append("  What we could NOT verify:")
        for r in (sc.unverified_reasons if sc else []):
            out.append(f"    - {r}")
        out.append("")
        out.append("  Bound read-only. No cost figure is shown: there is no")
        out.append("  budget profile for a primitive composition, and inventing")
        out.append("  one is forbidden.")
        out.append("")
        out.append("Before production use, answer:")
        for i, q in enumerate(result.questions, 1):
            out.append(f"  {i}. {q}")
        return "\n".join(out)

    # ---- three cards
    hdr("THREE WAYS TO BUILD THIS")
    names = {"minimal": "A · MINIMAL", "balanced": "B · BALANCED", "ambitious": "C · AMBITIOUS"}
    for i, c in enumerate(result.candidates):
        star = "   * RECOMMENDED" if c.recommended else ""
        out.append("")
        out.append(f"{names.get(c.axis, c.axis.upper())}{star}")
        out.append(f"  Worth it because: {beats_baseline(c, cat)}")
        out.append("")
        out.append(f"  1. {cat.pattern(c.tree.patterns()[0]).summary.strip().splitlines()[0]}")
        out.append(f"  2. {chips(c, cat)}")
        out.append(f"  3. ~EUR{c.cost_per_task:.4f}/task  ~{c.latency_s:.0f}s")
        out.append(f"     {band_note(c, ir)}" if ir else "")
        out.append(f"  4. Humans: {humans_sit(c, cat)}")
        out.append(f"  5. Top risk: {top_risk(c, cat)}")
        out.append(f"  6. Trade-off: {tradeoff(c, result.candidates, cat)}")
        if c.warnings:
            for w in c.warnings:
                out.append(f"     ! {w.reason[:90]}")
    b = result.baseline
    if b:
        out.append("")
        out.append("BASELINE (always compared)")
        out.append(f"  One grounded agent, no orchestration. ~EUR{b.cost_per_task:.4f}/task.")
        out.append("  Choose this if the options above cannot justify their cost.")
    return "\n".join(out)


def render_kill_log(result: Result, limit: int = 12) -> str:
    if not result.kill_log:
        return "No candidates were rejected."
    out = ["Rejected candidates (the most useful telemetry this produces):"]
    for sig, k in result.kill_log[:limit]:
        out.append(f"  {sig:32s} killed by {k.rule_id}")
        out.append(f"      {k.reason[:100]}")
        if k.repair:
            out.append(f"      repair: {k.repair[:100]}")
    return "\n".join(out)
