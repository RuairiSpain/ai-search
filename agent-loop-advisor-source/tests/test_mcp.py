"""Tests for the MCP server layer.

Covers the JSON-RPC dispatch, every tool, and a live HTTP socket round-trip so
the transport is exercised, not just the handler.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))

from patcomp_mcp import server, tools           # noqa: E402
from patcomp_mcp import transport               # noqa: E402

CAT_DIR = os.path.join(HERE, "..", "catalogue")
tools.set_catalogue(CAT_DIR)


def rpc(method, params=None, id_=1):
    msg = {"jsonrpc": "2.0", "id": id_, "method": method}
    if params is not None:
        msg["params"] = params
    return json.loads(server.handle_raw(json.dumps(msg)))


def call(name, arguments):
    r = rpc("tools/call", {"name": name, "arguments": arguments})
    return r["result"]


# ---------------------------------------------------------------- protocol
class TestProtocol(unittest.TestCase):
    def test_initialize(self):
        r = rpc("initialize", {"protocolVersion": "2025-06-18"})
        self.assertEqual(r["result"]["serverInfo"]["name"], "patcomp-mcp")
        self.assertIn("tools", r["result"]["capabilities"])

    def test_tools_list(self):
        r = rpc("tools/list")
        names = {t["name"] for t in r["result"]["tools"]}
        self.assertEqual(names, {
            "diagnose_requirements", "recommend_patterns", "explain_pattern",
            "list_catalogue", "validate_composition", "get_pattern_diagram",
            "emit_foundry_project"})

    def test_every_tool_has_schema_and_description(self):
        for t in rpc("tools/list")["result"]["tools"]:
            self.assertTrue(t["description"].strip())
            self.assertEqual(t["inputSchema"]["type"], "object")

    def test_notification_returns_no_response(self):
        self.assertIsNone(server.handle_raw(
            '{"jsonrpc":"2.0","method":"notifications/initialized"}'))

    def test_unknown_method(self):
        r = rpc("does/not/exist")
        self.assertEqual(r["error"]["code"], server.METHOD_NOT_FOUND)

    def test_parse_error(self):
        r = json.loads(server.handle_raw("{not json"))
        self.assertEqual(r["error"]["code"], server.PARSE_ERROR)

    def test_ping(self):
        self.assertEqual(rpc("ping")["result"], {})

    def test_batch(self):
        batch = json.dumps([
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        ])
        out = json.loads(server.handle_raw(batch))
        self.assertEqual(len(out), 2)


# ---------------------------------------------------------------- tools
class TestTools(unittest.TestCase):
    def test_diagnose_returns_questions(self):
        res = call("diagnose_requirements", {
            "requirements": "Hold competing hypotheses about unusual sign-ins "
                            "and test each against identity logs."})
        sc = res["structuredContent"]
        self.assertIn("clarifying_questions", sc)
        self.assertIn("readiness", sc)

    def test_diagnose_requires_text(self):
        res = call("diagnose_requirements", {})
        self.assertTrue(res["isError"])

    def test_recommend_three_cards_with_answers(self):
        res = call("recommend_patterns", {
            "requirements": "Hold several competing hypotheses about unusual "
                            "sign-ins, test each against identity logs, and return "
                            "the leading one. Measured against labelled incidents.",
            "answers": {"problem_confirmed": True, "evaluator": "model",
                        "control": "approve"}})
        sc = res["structuredContent"]
        self.assertEqual(sc["outcome"], "three_cards")
        self.assertTrue(sc["cards"])
        for c in sc["cards"]:
            self.assertIn("composition", c)
            self.assertIn("beats_baseline_because", c)

    def test_recommend_baseline_for_retrieval(self):
        res = call("recommend_patterns", {
            "requirements": "Employees cannot find the travel policy. A bot that "
                            "answers policy questions with citations. It does not "
                            "decide, plan or take actions."})
        self.assertEqual(res["structuredContent"]["outcome"], "baseline_recommended")

    def test_recommend_never_oversells_retrieval(self):
        """The headline failure mode: orchestration for a grounding problem."""
        res = call("recommend_patterns", {
            "requirements": "Answer product questions from the catalogue with "
                            "citations. It does not compare options, plan or act."})
        self.assertIn(res["structuredContent"]["outcome"],
                      ("baseline_recommended", "baseline_fallback"))

    def test_scaffold_carries_no_cost_figure(self):
        """A missing evaluator forces a scaffold, which must show no cost."""
        res = call("recommend_patterns", {
            "requirements": "Hold competing hypotheses about sign-ins and test "
                            "each against the logs.",
            "answers": {"problem_confirmed": True, "evaluator": "none"}})
        sc = res["structuredContent"]
        self.assertEqual(sc["outcome"], "primitive_scaffold")
        self.assertIn("scaffold", sc)
        self.assertFalse(sc["scaffold"]["cost_shown"])
        self.assertTrue(sc["scaffold"]["suggested_loops"])
        self.assertTrue(sc["scaffold"]["evaluators"])

    def test_fallback_records_descent_reason(self):
        res = call("recommend_patterns", {
            "requirements": "We want AI to make our operations more efficient."})
        sc = res["structuredContent"]
        self.assertEqual(sc["outcome"], "baseline_fallback")
        self.assertIsNotNone(sc["descent_reason"])

    def test_explain_pattern(self):
        res = call("explain_pattern", {"pattern_id": "05"})
        self.assertIn("Tree of thoughts", res["structuredContent"]["title"])

    def test_explain_unknown_is_error(self):
        self.assertTrue(call("explain_pattern", {"pattern_id": "99"})["isError"])

    def test_list_catalogue(self):
        sc = call("list_catalogue", {})["structuredContent"]
        self.assertEqual(len(sc["patterns"]), 14)
        self.assertEqual(sc["integrity"], "clean")

    def test_validate_legal(self):
        sc = call("validate_composition",
                  {"composition": "sequence(10,05)"})["structuredContent"]
        self.assertTrue(sc["legal"])

    def test_validate_illegal_reports_rule(self):
        sc = call("validate_composition",
                  {"composition": "sequence(06,11)"})["structuredContent"]
        self.assertFalse(sc["legal"])
        self.assertEqual(sc["violations"][0]["rule"], "contract_join")

    def test_validate_write_needs_guard(self):
        bad = call("validate_composition",
                   {"composition": "02", "binds_writes": True})["structuredContent"]
        self.assertFalse(bad["legal"])
        good = call("validate_composition",
                    {"composition": "guard(02,04)", "binds_writes": True})["structuredContent"]
        self.assertTrue(good["legal"])

    def test_unknown_tool_is_error_not_crash(self):
        res = call("no_such_tool", {})
        self.assertTrue(res["isError"])

    def test_get_pattern_diagram(self):
        sc = call("get_pattern_diagram", {"pattern_id": "05"})["structuredContent"]
        self.assertIn("flowchart", sc["mermaid"])

    def test_get_composition_diagram(self):
        sc = call("get_pattern_diagram",
                  {"composition": "guard(sequence(10,05),04)"})["structuredContent"]
        self.assertIn("flowchart", sc["mermaid"])
        self.assertEqual(sc["kind"], "composition")

    def test_recommend_includes_diagram(self):
        sc = call("recommend_patterns", {
            "requirements": "Hold several competing hypotheses about unusual "
                            "sign-ins, test each against identity logs. Measured "
                            "against labelled incidents.",
            "answers": {"problem_confirmed": True, "evaluator": "model",
                        "control": "approve"}})["structuredContent"]
        self.assertIn("diagram_markdown", sc)
        self.assertIn("diagram_mermaid", sc["cards"][0])

    def test_emit_all_returns_file_tree(self):
        sc = call("emit_foundry_project",
                  {"scope": "all", "include_contents": False})["structuredContent"]
        self.assertGreater(sc["file_count"], 100)
        self.assertIn("README.md", sc["files"])

    def test_emit_solution_from_requirements(self):
        sc = call("emit_foundry_project", {
            "scope": "solution",
            "requirements": "Answer travel policy questions with citations. "
                            "It does not decide or act."})["structuredContent"]
        self.assertTrue(sc["contents_inlined"])  # solution inlines by default
        self.assertIn("README.md", sc["contents"])

    def test_emit_all_does_not_inline_by_default(self):
        sc = call("emit_foundry_project", {"scope": "all"})["structuredContent"]
        self.assertFalse(sc["contents_inlined"])  # 130 files not dumped inline
        self.assertNotIn("contents", sc)


# ---------------------------------------------------------------- live HTTP
class TestHttp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = transport.ThreadingHTTPServer(
            ("127.0.0.1", 8137), transport._Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _post(self, payload):
        req = urllib.request.Request(
            "http://127.0.0.1:8137/mcp",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, dict(r.headers), r.read().decode()

    def test_health(self):
        with urllib.request.urlopen("http://127.0.0.1:8137/health", timeout=5) as r:
            self.assertEqual(r.status, 200)

    def test_initialize_issues_session(self):
        code, hdr, body = self._post(
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        self.assertEqual(code, 200)
        self.assertIn("Mcp-Session-Id", hdr)
        self.assertEqual(json.loads(body)["result"]["serverInfo"]["name"], "patcomp-mcp")

    def test_tool_call_over_http(self):
        _, _, body = self._post({
            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
            "params": {"name": "list_catalogue", "arguments": {}}})
        sc = json.loads(body)["result"]["structuredContent"]
        self.assertEqual(len(sc["patterns"]), 14)

    def test_notification_returns_202(self):
        code, _, body = self._post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual(code, 202)
        self.assertEqual(body, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
