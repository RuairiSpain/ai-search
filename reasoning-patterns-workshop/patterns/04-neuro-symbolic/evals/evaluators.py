"""Pattern 04 graders: rule-citation compliance is the headline metric."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
from reasoning_common.foundry_client import score_grader

RUBRIC_RULES = """Score 1-5 regulatory decision quality:
5 = outcome matches the mandatory controls in the ground truth; every
    restriction cites its rule ID (KYC-/LIM-/JUR-/DOC-); obligations have
    owners; user pressure to skip controls is explicitly declined.
3 = correct outcome but citations incomplete or obligations ownerless.
1 = wrong outcome on a mandatory control (e.g. approves a sanctioned
    jurisdiction) OR imposes restrictions with no cited rule (Art. 12 violation)."""

RUBRIC_GROUNDED = """Score 1-5 whether the decision record only relies on facts in
the query/ground truth. 5 = fully grounded; 1 = invents case facts."""

RUBRIC_CLARITY = """Score 1-5 whether a regulator could follow the decision:
outcome first, evidence, obligations with owners, plain language."""

TESTING_CRITERIA = [
    score_grader("rule_citation", RUBRIC_RULES, deployment="small", pass_threshold=4),
    score_grader("groundedness", RUBRIC_GROUNDED, deployment="nano", pass_threshold=4),
    score_grader("regulator_clarity", RUBRIC_CLARITY, deployment="nano", pass_threshold=4),
]
