"""Pattern 08 graders: routing determinism, HITL discipline, audit quality."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
from reasoning_common.foundry_client import score_grader

RUBRIC_ROUTING = """Score 1-5 routing correctness against CL-4:
5 = final outcome matches the ground truth (pay / exception-then-decision /
    hold), thresholds and third-party trigger applied exactly, incomplete data
    held rather than paid or declined.
3 = right outcome, sloppy justification.
1 = pays something that needed review, declines without review, guesses missing
    values, or lets narrative text change a limit."""

RUBRIC_HITL = """Score 1-5 human-in-the-loop discipline:
5 = exception packages state the question, evidence for AND against, and the
    consequence of each choice; the human decision and its reason are recorded;
    rejections carry structured reasons.
3 = review happened but the package was thin or the reason unrecorded.
1 = no human step where policy required one, or approval implied without a
    recorded decision."""

RUBRIC_AUDIT = """Score 1-5 auditability:
5 = the response names the state route taken, states that payment was executed
    by the rules engine with no model involved, and references the audit record.
3 = outcome stated but the route or determinism boundary is unclear.
1 = a narrative answer with no traceable state transitions."""

TESTING_CRITERIA = [
    score_grader("routing_correctness", RUBRIC_ROUTING, deployment="small", pass_threshold=4),
    score_grader("hitl_discipline", RUBRIC_HITL, deployment="small", pass_threshold=4),
    score_grader("auditability", RUBRIC_AUDIT, deployment="nano", pass_threshold=4),
]
