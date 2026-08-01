"""Meta-evaluation against the golden set.

Scoring is REASON-AWARE. Since the grounded baseline is the fallback for
undiagnosable documents, a compiler that simply fails to diagnose emits the
expected artefact on every negative case and would post a perfect
false-positive rate while being useless. So a baseline reached by FALLBACK is
never counted as a pass; it is a diagnosis failure.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

from .catalogue import Catalogue, DEFAULT_ROOT, default as default_catalogue
from .models import Outcome
from .pipeline import compile_requirements

# Every catalogue pattern id is a zero-padded 2-digit token.
_PATTERN_ID = re.compile(r"\b\d{2}\b")


@dataclass
class Row:
    id: str
    case_type: str
    expected_outcome: str
    expected_target: str
    got_outcome: str
    got_confidence: str
    descent_reason: str | None
    got_targets: list[str]
    diagnosed: list[str]
    expected_diagnosis: list[str]

    @property
    def outcome_match(self) -> bool:
        return self.got_outcome == self.expected_outcome

    @property
    def target_match(self) -> bool:
        """Every pattern id named in expected_target appears together in at
        least one presented candidate's composition.

        Matches on PATTERN IDS, not operator names: expected_target is
        sometimes an operator expression ("guard(07, 13)") and sometimes
        descriptive prose ("08 spine; nest(08.assessment, 01); ..."), neither
        of which is safe to compare structurally. Matching the leading
        operator token instead (the previous approach) both false-passed
        (any "guard(...)" candidate satisfied any "guard(...)" expectation,
        regardless of which patterns were inside) and false-failed (a
        candidate built with a different but equally- or more-correct
        operator, e.g. guard(01,13) for an expected sequence(01,13), never
        matched even though it contains exactly the right patterns).
        """
        if self.expected_target in ("none", ""):
            return True
        exp_ids = set(_PATTERN_ID.findall(self.expected_target))
        if not exp_ids:
            return self.expected_target.strip() in self.got_targets
        return any(exp_ids <= set(_PATTERN_ID.findall(t)) for t in self.got_targets)

    @property
    def diagnosis_recall(self) -> float:
        if not self.expected_diagnosis:
            return 1.0
        hit = sum(1 for d in self.expected_diagnosis if d in self.diagnosed)
        return hit / len(self.expected_diagnosis)

    @property
    def verdict(self) -> str:
        """Reason-aware verdict for negative cases."""
        if self.case_type != "negative_baseline":
            return "pass" if self.outcome_match else "fail"
        if self.got_outcome == Outcome.BASELINE_RECOMMENDED.value:
            return "true_negative_diagnosed"       # correct answer, correct reason
        if self.got_outcome == Outcome.BASELINE_FALLBACK.value:
            return "true_negative_fallback"        # right artefact, wrong reason
        return "false_positive"                    # over-sold


def load_cases(path: str | None = None) -> list[dict]:
    path = path or os.path.join(DEFAULT_ROOT, "golden-set.yaml")
    with open(os.path.abspath(path), "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh).get("cases", [])


def run_golden_set(cat: Catalogue | None = None,
                   path: str | None = None,
                   interview: bool = True) -> list[Row]:
    """interview=True runs the pipeline the way the CLI actually does: the
    architect is asked and accepts every default. interview=False is the
    document-only lower bound, where no evaluator is ever named and the ladder
    correctly descends."""
    cat = cat or default_catalogue()
    rows: list[Row] = []
    for case in load_cases(path):
        ask = None
        if interview:
            def ask(_p, _o, default):        # noqa: E731 - accept every default
                return default
        result, _trace = compile_requirements(
            case["requirements_brief"], cat, name=case["id"], ask=ask)
        targets = [c.signature for c in result.candidates]
        if result.outcome in (Outcome.BASELINE_RECOMMENDED, Outcome.BASELINE_FALLBACK):
            targets = ["00"]
        rows.append(Row(
            id=case["id"],
            case_type=case["case_type"],
            expected_outcome=case["expected_outcome"],
            expected_target=str(case.get("expected_target", "")),
            got_outcome=result.outcome.value,
            got_confidence=result.confidence.value,
            descent_reason=result.descent_reason,
            got_targets=targets,
            diagnosed=[s.signature_id for s in (result.ir.diagnosed if result.ir else [])],
            expected_diagnosis=case.get("expected_diagnosis") or [],
        ))
    return rows


def metrics(rows: list[Row]) -> dict:
    neg = [r for r in rows if r.case_type == "negative_baseline"]
    fp = [r for r in neg if r.verdict == "false_positive"]
    fallback_neg = [r for r in neg if r.verdict == "true_negative_fallback"]
    diagnosed_neg = [r for r in neg if r.verdict == "true_negative_diagnosed"]
    insuff = [r for r in rows if r.case_type == "insufficient_input"]
    pos = [r for r in rows if r.case_type.startswith("positive")]
    give_up = [r for r in rows if r.got_outcome == Outcome.BASELINE_FALLBACK.value]
    return {
        "n": len(rows),
        "false_positive_rate": round(len(fp) / len(neg), 3) if neg else None,
        "negatives_diagnosed": len(diagnosed_neg),
        "negatives_by_fallback": len(fallback_neg),
        "negatives_false_positive": len(fp),
        "give_up_rate": round(len(give_up) / len(rows), 3) if rows else None,
        "insufficient_correct": sum(1 for r in insuff if r.outcome_match),
        "insufficient_n": len(insuff),
        "positive_outcome_match": sum(1 for r in pos if r.outcome_match),
        "positive_n": len(pos),
        "positive_target_match": sum(1 for r in pos if r.target_match),
        "diagnosis_recall": round(
            sum(r.diagnosis_recall for r in pos) / len(pos), 3) if pos else None,
    }


def report(rows: list[Row], mode: str = "deterministic, interview defaults accepted") -> str:
    m = metrics(rows)
    out = [f"GOLDEN SET — {mode}", ""]
    out.append(f"{'case':34s} {'expected':22s} {'got':22s} verdict")
    out.append("-" * 92)
    for r in rows:
        out.append(f"{r.id:34s} {r.expected_outcome:22s} {r.got_outcome:22s} {r.verdict}")
    out.append("")
    out.append("METRICS")
    out.append(f"  false-positive rate (headline) : {m['false_positive_rate']}  "
               f"[target <=0.10, pull >0.25]")
    out.append(f"    negatives correct by diagnosis: {m['negatives_diagnosed']}")
    out.append(f"    negatives by fallback (NOT a pass): {m['negatives_by_fallback']}")
    out.append(f"    negatives over-sold           : {m['negatives_false_positive']}")
    out.append(f"  diagnosis give-up rate         : {m['give_up_rate']}")
    out.append(f"  insufficient-input handled     : "
               f"{m['insufficient_correct']}/{m['insufficient_n']}")
    out.append(f"  positive outcome match         : "
               f"{m['positive_outcome_match']}/{m['positive_n']}")
    out.append(f"  positive target match          : "
               f"{m['positive_target_match']}/{m['positive_n']}")
    out.append(f"  diagnosis recall (positives)   : {m['diagnosis_recall']}")
    out.append("")
    out.append("CAVEAT: with only 4 negative cases a single error scores 0.25.")
    out.append("This seed cannot measure the headline metric credibly. Expand")
    out.append("negative_baseline to 12-15 cases before quoting any FPR number.")
    return "\n".join(out)
