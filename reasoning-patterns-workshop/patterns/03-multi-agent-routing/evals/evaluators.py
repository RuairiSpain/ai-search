"""Pattern 03 graders: decision hygiene + numeric traceability."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "common"))
from reasoning_common.foundry_client import score_grader

RUBRIC_DECISION = """Score 1-5 the decision hygiene of the response:
5 = clear single recommendation; every number traceable to tool evidence;
    rejected alternatives listed WITH their figures; false premises corrected.
3 = right recommendation but alternatives missing or a figure unsourced.
1 = invented/unverified numbers, or builds on a false premise, or no clear
    recommendation."""

RUBRIC_NUMERIC = """Score 1-5 numeric correctness against the ground truth:
5 = all figures and any arithmetic exactly consistent with the ground truth;
3 = minor omission; 1 = any wrong or fabricated number."""

RUBRIC_RELEVANCE = """Score 1-5 how completely the response answers the question
asked, including the 'why not the others' part when requested."""

TESTING_CRITERIA = [
    score_grader("decision_hygiene", RUBRIC_DECISION, deployment="small", pass_threshold=4),
    score_grader("numeric_traceability", RUBRIC_NUMERIC, deployment="small", pass_threshold=4),
    score_grader("relevance", RUBRIC_RELEVANCE, deployment="nano", pass_threshold=4),
]
