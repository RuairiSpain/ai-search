"""Tests for diagram generation and the Foundry/MAF project emitter."""
from __future__ import annotations

import ast
import os
import py_compile
import sys
import tempfile
import unittest

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from patcomp import catalogue as cat_mod       # noqa: E402
from patcomp import diagram, emit              # noqa: E402
from patcomp.models import Node                # noqa: E402
from patcomp.pipeline import compile_requirements  # noqa: E402
from patcomp.tools_parse import parse_composition  # noqa: E402

CAT = cat_mod.load(os.path.join(HERE, "..", "catalogue"))


class TestDiagram(unittest.TestCase):
    def test_every_pattern_has_a_diagram(self):
        for pid in CAT.patterns:
            m = diagram.pattern_mermaid(pid)
            self.assertTrue(m.startswith("flowchart"), pid)

    def test_pattern_markdown_embeds_mermaid(self):
        md = diagram.pattern_markdown("05", CAT)
        self.assertIn("```mermaid", md)
        self.assertIn("Hypothesis", md)

    def test_composition_diagram_covers_all_operators(self):
        trees = {
            "sequence": Node.op("sequence", Node.leaf("10"), Node.leaf("05")),
            "guard": Node.op("guard", Node.leaf("02"), Node.leaf("04")),
            "nest": Node.op("nest", Node.leaf("08"), Node.leaf("05")),
            "fan": Node.op("fan", Node.leaf("03"), Node.leaf("01")),
            "substitute": Node.op("substitute", Node.leaf("01"), Node.leaf("11")),
        }
        for name, tree in trees.items():
            m = diagram.composition_mermaid(tree, CAT)
            self.assertIn("flowchart LR", m, name)
            # every leaf pattern id should be labelled somewhere
            for pid in tree.patterns():
                self.assertIn(pid, m, f"{name} missing {pid}")

    def test_composition_diagram_is_connected(self):
        tree = Node.op("guard", Node.op("sequence", Node.leaf("10"),
                                        Node.leaf("05")), Node.leaf("04"))
        m = diagram.composition_mermaid(tree, CAT)
        self.assertIn("START", m)
        self.assertIn("DONE", m)
        self.assertIn("-->", m)

    def test_primitives_diagram(self):
        m = diagram.primitives_mermaid(["branch_hypotheses", "constrain", "verify"])
        self.assertIn("UNVERIFIED", m)


class TestEmitCatalogue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.files = emit.emit_catalogue_project(CAT)

    def test_has_root_scaffolding(self):
        for f in ("README.md", "requirements.txt", ".env.sample",
                  "shared/foundry_client.py", "shared/maf_client.py",
                  "verify_structure.py", "infra/main.bicep", "azure.yaml",
                  "PROVENANCE.yaml", "ARCHITECTURE.md"):
            self.assertIn(f, self.files, f)

    def test_excludes_meta_and_guard_only(self):
        folders = {p.split("/")[1] for p in self.files if p.startswith("patterns/")}
        self.assertFalse(any(x.startswith("12-") for x in folders))  # meta
        self.assertFalse(any(x.startswith("13-") for x in folders))  # guard_only

    def test_every_pattern_folder_has_evaluator(self):
        folders = {p.split("/")[1] for p in self.files if p.startswith("patterns/")}
        for folder in folders:
            self.assertIn(f"patterns/{folder}/evaluators.py", self.files)

    def test_uses_microsoft_libraries(self):
        req = self.files["requirements.txt"]
        for lib in ("agent-framework", "azure-ai-projects", "azure-ai-evaluation",
                    "azure-identity"):
            self.assertIn(lib, req)

    def test_foundry_client_uses_default_credential(self):
        self.assertIn("DefaultAzureCredential", self.files["shared/foundry_client.py"])
        self.assertIn("AIProjectClient", self.files["shared/foundry_client.py"])

    def test_all_generated_python_compiles(self):
        with tempfile.TemporaryDirectory() as d:
            emit.write_files(self.files, d)
            for root, _dirs, names in os.walk(d):
                for n in names:
                    if n.endswith(".py"):
                        py_compile.compile(os.path.join(root, n), doraise=True)

    def test_all_generated_yaml_parses(self):
        for path, content in self.files.items():
            if path.endswith(".yaml"):
                yaml.safe_load(content)

    def test_emitted_project_passes_its_own_gate(self):
        with tempfile.TemporaryDirectory() as d:
            emit.write_files(self.files, d)
            # run the emitted verify_structure.py in-process
            ns: dict = {"__file__": os.path.join(d, "verify_structure.py"),
                        "__name__": "not_main"}
            src = self.files["verify_structure.py"].replace(
                'if problems:', 'RESULT = problems\nif False and problems:')
            exec(compile(src, "verify", "exec"), ns)  # noqa: S102
            self.assertEqual(ns.get("RESULT"), [], ns.get("RESULT"))

    def test_agent_stub_defaults_tools_readonly(self):
        stub = next(v for k, v in self.files.items()
                    if k.endswith(".py") and "/agents/" in k)
        self.assertIn("tools=[]", stub)  # no write tools bound by default
        self.assertIn("read-only", stub.lower())


class TestEmitSolution(unittest.TestCase):
    def _solution(self, text, answers=None):
        result, _ = compile_requirements(text, CAT, answers=answers)
        return result, emit.emit_solution_project(result, CAT, "sol")

    def test_solution_only_includes_involved_patterns(self):
        _r, files = self._solution(
            "Investigate fraud by holding competing hypotheses about a ring and "
            "enforcing mandatory controls before any recommendation. Measured "
            "against labelled cases.",
            {"problem_confirmed": True, "evaluator": "human", "control": "approve"})
        folders = {p.split("/")[1] for p in files if p.startswith("patterns/")}
        self.assertIn("orchestration.py", files)
        self.assertTrue(folders)
        # should not scaffold the entire catalogue
        self.assertLess(len(folders), 6)

    def test_solution_orchestration_compiles(self):
        _r, files = self._solution(
            "Hold competing hypotheses about sign-ins and test each against logs. "
            "Measured against labelled incidents.",
            {"problem_confirmed": True, "evaluator": "model", "control": "approve"})
        with tempfile.TemporaryDirectory() as d:
            emit.write_files(files, d)
            py_compile.compile(os.path.join(d, "orchestration.py"), doraise=True)

    def test_scaffold_solution_carries_unverified_note(self):
        _r, files = self._solution(
            "Hold competing hypotheses about sign-ins and test each against logs.",
            {"problem_confirmed": True, "evaluator": "none"})
        self.assertIn("UNVERIFIED.md", files)

    def test_scaffold_solution_emits_the_diagnosed_primitives_not_the_baseline(self):
        """A PRIMITIVE_SCAFFOLD outcome must scaffold what was diagnosed
        (e.g. branch_hypotheses), never silently substitute pattern 00."""
        result, files = self._solution(
            "Hold competing hypotheses about sign-ins and test each against logs.",
            {"problem_confirmed": True, "evaluator": "none"})
        self.assertEqual(result.outcome.value, "primitive_scaffold")
        self.assertTrue(result.scaffold.primitives)
        for prim in result.scaffold.primitives:
            base = f"primitives/{prim.replace('_', '-')}"
            self.assertIn(f"{base}/agent.md", files)
            self.assertIn(f"{base}/agent.py", files)
        # the baseline may appear ONLY as a clearly-labelled comparison, never
        # as "the recommended composition"
        self.assertNotIn("Recommended composition:** `00`", files["README.md"])

    def test_scaffold_solution_every_file_opens_with_unverified_banner(self):
        _r, files = self._solution(
            "Hold competing hypotheses about sign-ins and test each against logs.",
            {"problem_confirmed": True, "evaluator": "none"})
        for path, content in files.items():
            if path.endswith(".md"):
                self.assertTrue(content.startswith("> ⚠ UNVERIFIED SCAFFOLD"), path)
            elif path.endswith(".py") and "/primitives/" in path:
                self.assertTrue(content.startswith("# ⚠ UNVERIFIED SCAFFOLD"), path)

    def test_scaffold_solution_emits_evaluator_todo_and_questions(self):
        _r, files = self._solution(
            "Hold competing hypotheses about sign-ins and test each against logs.",
            {"problem_confirmed": True, "evaluator": "none"})
        self.assertIn("EVALUATOR-TODO.md", files)
        self.assertIn("QUESTIONS.md", files)

    def test_low_confidence_fallback_is_marked_everywhere(self):
        """baseline_fallback must not read like a confident recommendation:
        the banner belongs on every emitted file, and PROVENANCE.yaml must
        record why (low_confidence_is_marked, operators.yaml, fatal)."""
        result, files = self._solution(
            "Things happen sometimes and we would like help.")
        self.assertEqual(result.outcome.value, "baseline_fallback")
        self.assertEqual(result.confidence.value, "low")
        self.assertIn("descent_reason:", files["PROVENANCE.yaml"])
        self.assertIn("recommendation_confidence: low", files["PROVENANCE.yaml"])
        self.assertTrue(files["README.md"].startswith("> ⚠ LOW CONFIDENCE"))
        agent_files = [v for k, v in files.items()
                       if k.endswith(".md") and "/agents/" in k]
        self.assertTrue(agent_files)
        for content in agent_files:
            self.assertTrue(content.startswith("> ⚠ LOW CONFIDENCE"))
        self.assertIn("EVALUATOR-TODO.md", files)
        self.assertIn("QUESTIONS.md", files)

    def test_confident_recommendation_carries_no_low_confidence_banner(self):
        """Face A (baseline_recommended, high confidence) must never carry the
        low-confidence banner — it emits the identical pattern-00 files as
        Face D, and the banner is the only thing telling them apart."""
        result, files = self._solution(
            "Employees cannot find the current travel-expense policy; answers "
            "are scattered across SharePoint and outdated PDFs. We want a bot "
            "that answers policy questions with citations to the current "
            "document. No decisions, approvals or actions are taken.",
            {"problem_confirmed": True})
        self.assertEqual(result.outcome.value, "baseline_recommended")
        self.assertEqual(result.confidence.value, "high")
        self.assertFalse(files["README.md"].startswith("> ⚠ LOW CONFIDENCE"))
        self.assertNotIn("descent_reason:", files["PROVENANCE.yaml"])

    def test_zip_bytes_are_a_valid_zip(self):
        import io
        import zipfile
        _r, files = self._solution(
            "Answer travel policy questions with citations. It does not act.")
        data = emit.zip_bytes(files, top="sol")
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertTrue(z.namelist())
        self.assertTrue(all(n.startswith("sol/") for n in z.namelist()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
