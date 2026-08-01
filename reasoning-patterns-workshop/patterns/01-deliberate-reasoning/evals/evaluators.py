"""Pattern 01 graders (OpenAI evals API, SDK-verified shape via
reasoning_common.foundry_client.score_grader). Each judge returns 1-5 with the
reasoning visible in the portal — the reasoning tells you what to fix."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
from reasoning_common.foundry_client import score_grader

RUBRIC_RB7 = """Score 1-5 how well the response complies with runbook RB-7 and
deliberate-reasoning discipline:
5 = names the correct first diagnostic step per evidence; phrased as a step not a
    confirmed cause; alternatives/rejections visible; pool check done or
    explicitly deferred with reason.
3 = plausible step but missing alternatives OR pool-check status unstated.
1 = commits to a confirmed cause without discriminating evidence, or violates a
    hard constraint (e.g. endorses production restart without rollback note)."""

RUBRIC_RELEVANCE = """Score 1-5 how directly the response addresses the incident in
the query. 5 = fully on-point and actionable; 3 = partially relevant; 1 = generic
or off-topic."""

RUBRIC_GROUNDED = """Score 1-5 whether every claim in the response is supported by
the query, the runbook, or the ground truth. 5 = fully supported; 1 = contains
invented facts or unsupported certainty."""

TESTING_CRITERIA = [
    score_grader("rb7_compliance", RUBRIC_RB7, deployment="small", pass_threshold=4),
    score_grader("relevance", RUBRIC_RELEVANCE, deployment="nano", pass_threshold=4),
    score_grader("groundedness", RUBRIC_GROUNDED, deployment="nano", pass_threshold=4),
]
