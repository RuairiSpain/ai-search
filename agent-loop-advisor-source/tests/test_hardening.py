"""Regression tests for the audit fixes — bugs caught during the review."""
from __future__ import annotations

import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from patcomp import catalogue as cat_mod          # noqa: E402
from patcomp import diagnose                       # noqa: E402
from patcomp.models import (IR, EvaluatorCandidate,  # noqa: E402
                            Field_, SignatureLabel, TaskClass)
from patcomp.pipeline import compile_requirements  # noqa: E402
from patcomp.route import route                    # noqa: E402
from patcomp.tools_parse import parse_composition  # noqa: E402

CAT = cat_mod.load(os.path.join(HERE, "..", "catalogue"))


def ir_with(sigs, **kw):
    ir = IR(task_classes=[TaskClass("primary", "decision")], **kw)
    ir.objective["outcome"] = Field_.sourced("x", "x")
    ir.evaluator_candidates = [EvaluatorCandidate("primary", "good", "hybrid")]
    ir.signatures = [SignatureLabel(s, "p", p, None, 0.9, True) for s, p in sigs]
    return ir


class TestRecommendationSemantics(unittest.TestCase):
    """FIX 1: 'recommended' is the best-fit composition, not the median cost."""

    def test_exactly_one_card_recommended(self):
        r = route(ir_with([("relationship_discovery", "10"),
                           ("multiple_interpretations", "05")]), CAT)
        flags = [c.recommended for c in r.candidates]
        self.assertEqual(sum(flags), 1, "exactly one card must be recommended")

    def test_recommended_covers_the_diagnosis(self):
        """The recommendation must include a diagnosed reasoning pattern — not a
        cheap unrelated one that merely happened to be the median cost."""
        ir = ir_with([("multiple_interpretations", "05")])
        r = route(ir, CAT)
        rec = next(c for c in r.candidates if c.recommended)
        self.assertIn("05", rec.tree.patterns())

    def test_axis_is_a_machinery_ladder(self):
        r = route(ir_with([("relationship_discovery", "10"),
                           ("multiple_interpretations", "05")]), CAT)
        axes = [c.axis for c in r.candidates]
        self.assertEqual(axes, ["minimal", "balanced", "ambitious"])
        costs = [c.cost_per_task for c in r.candidates]
        self.assertEqual(costs, sorted(costs), "cards must be ordered by cost")


class TestDiagnosePerf(unittest.TestCase):
    """FIX 2: the document is stemmed once per diagnosis, not once per term."""

    def test_stem_text_called_once_per_diagnosis(self):
        calls = {"n": 0}
        orig = diagnose.stem_text

        def counting(t):
            calls["n"] += 1
            return orig(t)

        diagnose.stem_text = counting
        try:
            diagnose.score_signatures("plan the migration order across systems "
                                      "with dependencies " * 20, CAT)
        finally:
            diagnose.stem_text = orig
        # once for the document; negated-span stems only add up if the doc has
        # negations, and this one has none.
        self.assertLessEqual(calls["n"], 2)


class TestParserRobustness(unittest.TestCase):
    """FIX 3+4: malformed input raises ValueError; unknown ids are rejected."""

    def test_unbalanced_raises(self):
        for bad in ("guard(02", "sequence(", "guard(02))", "nest(05,)"):
            with self.assertRaises(ValueError, msg=bad):
                parse_composition(bad, CAT)

    def test_empty_raises(self):
        with self.assertRaises(ValueError):
            parse_composition("", CAT)

    def test_unknown_operator_raises(self):
        with self.assertRaises(ValueError):
            parse_composition("frobnicate(05,04)", CAT)

    def test_unknown_pattern_id_raises(self):
        with self.assertRaises(ValueError):
            parse_composition("sequence(99,05)", CAT)

    def test_overlong_raises(self):
        with self.assertRaises(ValueError):
            parse_composition("x" * 5000, CAT)

    def test_deeply_nested_raises_not_crashes(self):
        bomb = "sequence(" * 50 + "05" + ")" * 50
        with self.assertRaises(ValueError):
            parse_composition(bomb, CAT)

    def test_valid_still_parses(self):
        self.assertEqual(
            parse_composition("guard(sequence(10,05),04)", CAT).signature(),
            "guard(sequence(10,05),04)")

    def test_no_catalogue_skips_id_validation(self):
        # without a catalogue, ids are not validated (used for display-only)
        self.assertEqual(parse_composition("sequence(99,05)").patterns(),
                         ["99", "05"])


class TestDeterminismGeneration(unittest.TestCase):
    """FIX 6: a must-be-deterministic task class yields a legal deterministic
    candidate, not a forced scaffold."""

    def test_deterministic_control_still_recommends(self):
        r, _ = compile_requirements(
            "Hold competing hypotheses about unusual sign-ins and test each "
            "against identity logs. Measured against labelled incidents.",
            CAT, answers={"problem_confirmed": True, "evaluator": "model",
                          "control": "deterministic"})
        self.assertEqual(r.outcome.value, "three_cards")
        # every shown candidate must satisfy the determinism boundary
        for c in r.candidates:
            has_det = any(CAT.pattern(p).is_deterministic_core
                          for p in c.tree.patterns())
            self.assertTrue(has_det, c.signature)


if __name__ == "__main__":
    unittest.main(verbosity=2)
