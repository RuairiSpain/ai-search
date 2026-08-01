"""Program synthesis & test-repair (§16): the deliverable is a green test run.

Loop: generate config.py → run pytest in an isolated workspace → feed
tracebacks to the analyst → repair → revalidate, bounded by repair_rounds.
Two guarantees hold regardless of what any model outputs:
  1. Model output only ever lands in config.py (structural: the harness does
     the writing) — it *cannot* touch tests.
  2. Test files are sha256-checksummed every round; a mismatch aborts the run
     as tampering. Belt and braces, and the §14 echo inside §16.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common import sandbox  # noqa: E402
from reasoning_common.text_utils import strip_code_fences  # noqa: E402
from reasoning_common.budgets import Budget, BudgetExceeded  # noqa: E402
from reasoning_common.config import load_budgets  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag, span  # noqa: E402

FIXTURE = PATTERN_DIR / "fixture"
LEGACY = (FIXTURE / "legacy_config" / "parser.py").read_text(encoding="utf-8")
TESTS_SRC = FIXTURE / "tests" / "test_migrated.py"


def _instr(name: str) -> str:
    return (PATTERN_DIR / "agents" / name / "instructions.md").read_text(encoding="utf-8")


def _skill() -> str:
    return (PATTERN_DIR / "skills" / "minimal-diffs" / "SKILL.md").read_text(encoding="utf-8")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_pytest(workspace: Path, subset: list[str] | None) -> tuple[bool, str]:
    """Execute the acceptance suite against MODEL-GENERATED config.py.

    Runs through reasoning_common.sandbox (stripped env/secrets, no network,
    no subprocess spawn, resource ceilings) rather than a bare subprocess.run:
    the module under test was written by an LLM from an adversarial prompt in
    p11-03/p11-05's eval rows, and this is the point in the loop where that
    code actually executes on the workshop machine.
    """
    args = ["-m", "pytest", "-q", "--tb=short", "test_migrated.py"]
    if subset:
        args += ["-k", " or ".join(subset)]
    rc, output = sandbox.run_python(args, workspace, timeout_s=60)
    return rc == 0, output[-4000:]


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p11"))
    telemetry_init("pattern-11-synthesis")
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p11")
    trace: dict = {"rounds": [], "tests_passed": False}

    workspace = Path(tempfile.mkdtemp(prefix="p11-"))
    shutil.copy(TESTS_SRC, workspace / "test_migrated.py")
    tests_sha = _sha(workspace / "test_migrated.py")

    guidance = ""
    code = ""
    diff_text = ""
    try:
        for round_no in range(cfg["repair_rounds"] + 1):
            with span("p11.generate", round=round_no, variant=cfg["_variant_name"]):
                budget.charge()
                res = fc.chat(cfg["gen_deployment"], [
                    {"role": "system", "content": _instr("patch-generator") + "\n\n" + _skill()},
                    {"role": "user", "content": (
                        f"Task from user:\n{query}\n\n"
                        f"LEGACY SOURCE (parser.py):\n{LEGACY}\n\n"
                        f"ACCEPTANCE TESTS (read-only):\n{TESTS_SRC.read_text()}\n\n"
                        + (f"PREVIOUS ATTEMPT:\n{code}\n\nREPAIR GUIDANCE:\n{guidance}"
                           if guidance else "First attempt."))},
                ], max_output_tokens=1200)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, f"generate[{round_no}]")
                code = strip_code_fences(res.text) + "\n"
                # Guarantee 1: harness writes; model output can ONLY become config.py
                (workspace / "config.py").write_text(code, encoding="utf-8")

            # Guarantee 2: tests untouched, every round
            if _sha(workspace / "test_migrated.py") != tests_sha:
                return {"response": "ABORTED: test files changed mid-run — tampering guard "
                                    "tripped. This should be impossible via the harness; "
                                    "investigate the workspace.",
                        "trace": {**trace, "tampering": True}}

            with span("p11.test", round=round_no):
                passed, output = _run_pytest(workspace, cfg.get("test_subset"))
            trace["rounds"].append({"round": round_no, "passed": passed,
                                    "tail": output.splitlines()[-3:]})
            if passed:
                trace["tests_passed"] = True
                break
            if round_no >= cfg["repair_rounds"]:
                break

            with span("p11.analyze", round=round_no):
                budget.charge()
                analysis, res = fc.chat_json(cfg["analyst_deployment"], [
                    {"role": "system", "content": _instr("failure-analyst")},
                    {"role": "user", "content": f"pytest output:\n{output}\n\nCurrent code:\n{code}"},
                ], max_output_tokens=500)
                budget.charge(calls=0, tokens=res.input_tokens + res.output_tokens)
                ledger.add_result(res, f"analyze[{round_no}]")
                guidance = json.dumps(analysis, indent=1)

        diff_text = "".join(difflib.unified_diff(
            LEGACY.splitlines(keepends=True), code.splitlines(keepends=True),
            fromfile="legacy_config/parser.py", tofile="config.py"))
        out_dir = PATTERN_DIR / "runs"
        out_dir.mkdir(exist_ok=True)
        (out_dir / f"{ledger.run_tag}-config.py").write_text(code)
        (out_dir / f"{ledger.run_tag}.diff").write_text(diff_text)

        subset_note = (f" (SUBSET of {len(cfg['test_subset'])} tests — see weak-tests "
                       "discussion in README)" if cfg.get("test_subset") else "")
        if trace["tests_passed"]:
            response = (f"VALIDATED: all tests pass{subset_note} after "
                        f"{len(trace['rounds'])} attempt(s). "
                        f"Deliverable: runs/{ledger.run_tag}-config.py + .diff "
                        f"({len(diff_text.splitlines())} diff lines). "
                        "Note: model output can only land in config.py and tests are "
                        "checksummed per round — a green run means the code moved, "
                        "not the goalposts.")
        else:
            last = trace["rounds"][-1]["tail"] if trace["rounds"] else []
            response = (f"NOT VALIDATED after {len(trace['rounds'])} attempt(s): tests still "
                        f"failing{subset_note}. ESCALATE with best attempt + failures: "
                        f"{' | '.join(last)}. Do not ship unvalidated artefacts as if "
                        "validated (§16).")
        return {"response": response, "trace": {**trace, "budget": budget.snapshot()}}

    except BudgetExceeded as e:
        return {"response": f"ESCALATED: budget exhausted mid-loop ({e}); "
                            f"{len(trace['rounds'])} rounds recorded, none shipped.",
                "trace": {**trace, "budget": budget.snapshot(), "escalated": True}}
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    from reasoning_common.config import load_variant
    cfg = load_variant(PATTERN_DIR)
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p11-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger)
    print(out["response"])
    print("\n--- rounds ---\n" + json.dumps(out["trace"]["rounds"], indent=2))
    print("\n--- cost ---\n" + json.dumps(ledger.summary()["by_deployment"], indent=2))
    ledger.dump(PATTERN_DIR / "runs")
