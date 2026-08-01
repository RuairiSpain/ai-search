"""Pattern 02: the ReAct loop is owned by the Foundry agent runtime (declarative
prompt agent). This module deploys/updates the agent from git-versioned
markdown and triggers runs. run_case() is the shared eval-runner contract.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from reasoning_common import foundry_client as fc  # noqa: E402
from reasoning_common.caching import ContentCache  # noqa: E402
from reasoning_common.config import load_budgets, shared_env  # noqa: E402
from reasoning_common.costs import CostLedger  # noqa: E402
from reasoning_common.telemetry import init as telemetry_init, new_run_tag  # noqa: E402

_agent_cache = ContentCache()


def _instructions(cfg: dict) -> Path:
    """Concatenate instructions + skills into a temp file for upsert."""
    parts = [(PATTERN_DIR / cfg["instructions_file"]).read_text()]
    parts += [(PATTERN_DIR / s).read_text() for s in cfg.get("skills", [])]
    tmp = PATTERN_DIR / "runs" / f"_composed-{cfg['_variant_name']}.md"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text("\n\n---\n\n".join(parts))
    return tmp


def ensure_agent(cfg: dict) -> str:
    """Idempotently create/update the hosted agent for this variant.

    Tools: real MCP server (least-privilege allowlist from the variant) plus
    the contoso-policies knowledge index when attach_knowledge is true.

    Cached by ContentCache, keyed on the agent name AND a hash of everything
    that determines its registration (composed instructions, tool config) —
    editing instructions.md or a skill file and re-running in the same
    process re-registers instead of silently reusing the pre-edit agent.
    """
    name = cfg["agent_name"]
    env = shared_env()
    tools: list[dict] = [
        fc.mcp_tool("contoso-enterprise", env["MCP_SERVER_URL"],
                    allowed_tools=cfg["mcp_tools"],
                    require_approval=cfg.get("approval", "never")),
    ]
    tool_resources = None
    if cfg.get("attach_knowledge", True):
        kb = fc.knowledge_tool("contoso-policies")
        if kb is None:
            print("WARN: SEARCH_CONNECTION_ID not set — agent deployed WITHOUT the "
                  "knowledge base (see TROUBLESHOOTING.md 'Knowledge tool'). "
                  "MCP tools still work; groundedness scores will resemble the "
                  "no-knowledge variant until the connection exists.")
        else:
            kb_tools, tool_resources = kb
            tools.extend(kb_tools)
    instructions_path = _instructions(cfg)
    content = instructions_path.read_text() + "\n" + json.dumps(tools, sort_keys=True) \
        + "\n" + json.dumps(tool_resources, sort_keys=True)

    def _register() -> str:
        return fc.upsert_agent(
            name,
            deployment=cfg["agent_deployment"],
            instructions_path=instructions_path,
            tools=tools,
            tool_resources=tool_resources,
            description="Pattern 02 ReAct renewals copilot",
        )

    return _agent_cache.get_or_create(name, content, _register)


def run_case(query: str, cfg: dict, ledger: CostLedger | None = None,
             on_tool_approval=None) -> dict:
    ledger = ledger or CostLedger(new_run_tag("p02"))
    telemetry_init("pattern-02-react")
    budgets = load_budgets(PATTERN_DIR)
    agent_id = ensure_agent(cfg)
    approver = select_approver(cfg, on_tool_approval)
    result = fc.run_agent(agent_id, query, timeout_s=int(budgets["max_wall_clock_s"]),
                          on_tool_approval=approver)
    usage = result.get("usage") or {}
    ledger.add(cfg["agent_deployment"],
               usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
               step="agent_run")
    tool_calls = _extract_tool_calls(result["steps"])
    return {"response": result["final"],
            "trace": {"thread_id": result["thread_id"], "run_id": result["run_id"],
                      "status": str(result["status"]), "tool_calls": tool_calls,
                      "approvals": result.get("approvals", [])}}


def _extract_tool_calls(steps: list) -> list[str]:
    calls = []
    for s in steps:
        text = json.dumps(s) if not isinstance(s, str) else s
        for tool in ("get_account", "list_tickets", "get_contract_terms", "draft_offer"):
            if tool in text and tool not in calls:
                calls.append(tool)
    return calls


def _policy_approver(cfg: dict):
    """Deterministic stand-in reviewer, so approval-gated variants are
    EVALUABLE rather than erroring.

    Policy: approve `draft_offer` only up to the reviewer's own comfort
    threshold; above it, reject with a structured reason that the agent must
    revise against. That makes `make eval VARIANT=steerable` measure the thing
    the pattern actually claims — reject-with-reason steers the trajectory —
    instead of parking the run until the wall-clock guard kills it.
    """
    limit = float((cfg.get("review_policy") or {}).get("max_auto_approve_discount_pct", 10))

    def _approve(name: str, arguments) -> tuple[bool, str]:
        args = arguments
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        pct = float((args or {}).get("discount_pct", 0) or 0)
        if name != "draft_offer":
            return True, "read-only call"
        if pct <= limit:
            return True, f"reviewed: {pct}% within reviewer threshold {limit}%"
        return False, (f"{pct}% exceeds the reviewer's {limit}% threshold without a "
                       "remediation commitment. Re-draft at or below the threshold and "
                       "reference the open Sev-A incidents per CP-12 §3.3.")

    _approve.__name__ = "policy_approver"
    return _approve


def select_approver(cfg: dict, explicit=None, *, interactive: bool = False):
    """Injection wins; config never implies interactivity (see pattern 08 for
    the same rule). Approval-gated variants fall back to the policy approver so
    headless runs are deterministic instead of blocked."""
    if explicit is not None:
        return explicit
    gated = cfg.get("approval") not in (None, "never")
    if interactive and gated and sys.stdin.isatty():
        return _cli_approver
    return _policy_approver(cfg) if gated else None


def _cli_approver(name: str, arguments) -> tuple[bool, str]:
    """The human at the boundary. Structured rejection = learning signal (§13)."""
    if not sys.stdin.isatty():
        print("WARN: no TTY — approval gate is unattended.", file=sys.stderr)
        return True, "auto-approved: no reviewer present"
    print(f"\n⏸  APPROVAL REQUIRED — agent wants to call: {name}")
    print(f"   arguments: {arguments}")
    ans = input("   approve? [y/N]: ").strip().lower()
    if ans == "y":
        return True, ""
    reason = input("   rejection reason (be specific — it steers the revision): ").strip()
    return False, reason or "rejected without reason (this is noise, not signal)"


if __name__ == "__main__":
    import argparse
    from reasoning_common.config import load_variant
    ap = argparse.ArgumentParser()
    ap.add_argument("--interactive", action="store_true",
                    help="pause on gated tool calls (use VARIANT=steerable)")
    args = ap.parse_args()
    cfg = load_variant(PATTERN_DIR)
    if args.interactive and cfg["_variant_name"] != "steerable":
        print("hint: --interactive pairs with the steerable variant "
              "(VARIANT=steerable make run-interactive); reads won't pause otherwise.")
    sample = json.loads((PATTERN_DIR / "data" / "sample_input.json").read_text())
    ledger = CostLedger(new_run_tag(f"p02-{cfg['_variant_name']}"))
    out = run_case(sample["query"], cfg, ledger,
                   on_tool_approval=select_approver(cfg, None, interactive=args.interactive))
    if out["trace"].get("approvals"):
        print("\n--- approval decisions ---")
        print(json.dumps(out["trace"]["approvals"], indent=2))
    print(out["response"])
    print("\n--- tool calls observed ---", out["trace"]["tool_calls"])
    print("--- thread (open this in the portal Activity tab) ---", out["trace"]["thread_id"])
    ledger.dump(PATTERN_DIR / "runs")
