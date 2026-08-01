"""MAF-native version of pattern 05 using verified 1.12.1 primitives:
add_fan_out_edges (parallel branch expansion), add_fan_in_edges (score merge),
WorkflowViz (`make viz` renders the REAL built graph, not a hand-drawn one).
Business logic delegates to workflow.py so the two implementations cannot drift.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from agent_framework import WorkflowBuilder, WorkflowContext, WorkflowViz, executor  # type: ignore

from reasoning_common.budgets import Budget
from reasoning_common.config import load_budgets
from reasoning_common.costs import CostLedger

import workflow as impl


def build(cfg: dict, ledger: CostLedger, n_branches: int = 5):
    """One round expressed as fan-out → per-branch expand+score → fan-in.
    Multi-round loops are still driven by workflow.py (the round cap and
    prune decisions are policy, not graph shape); this file is for the
    parallelism primitive demonstration + WorkflowViz."""
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p05-maf")

    @executor(id="dispatch")
    async def dispatch(query: str, ctx: WorkflowContext[dict]) -> None:
        data, _ = impl.fc.chat_json(cfg["hypo_deployment"], [
            {"role": "system", "content": impl._instr("hypothesis-generator") + "\n\n" + impl._skill()},
            {"role": "user", "content": f"Alert:\n{query}\n\nGenerate {n_branches} hypotheses."},
        ], max_output_tokens=500)
        ctx.set_state("query", query)
        for i, h in enumerate(data.get("hypotheses", [])[:n_branches]):
            await ctx.send_message({"branch_idx": i, "hypothesis": h, "log": []})

    def _make_branch(idx: int):
        """Factory: each branch executor must expose EXACTLY (message, ctx) —
        MAF validates the signature, so the closure has to capture `idx` here
        rather than via a default argument (which counts as a third param)."""

        @executor(id=f"branch_{idx}")
        async def _branch(branch: dict, ctx: WorkflowContext[dict]) -> None:
            q = ctx.get_state("query") or ""
            # Each MAF branch executor gets its own throwaway trace dict and
            # lock — unlike workflow.py's ThreadPoolExecutor round, these
            # executors don't share trace state with siblings, so there's
            # nothing for the lock to actually contend on here; it's passed
            # only to satisfy _expand's signature (single source of truth
            # for the branch-expansion logic, shared with workflow.py).
            b = impl._expand(branch, q, cfg, budget, ledger, trace={}, trace_lock=threading.Lock())
            b["_score"] = impl._score_branch(b, cfg, budget, ledger)
            await ctx.send_message(b)

        return _branch

    branches = [_make_branch(i) for i in range(n_branches)]

    @executor(id="merge")
    async def merge(scored: list[dict], ctx: WorkflowContext[None, str]) -> None:
        ranked = sorted(scored, key=lambda b: -b["_score"])
        await ctx.yield_output("Round complete. Ranked branches: "
                               + " | ".join(f"{b['hypothesis']['id']}={b['_score']:.1f}"
                                            for b in ranked))

    builder = WorkflowBuilder(start_executor=dispatch)
    builder.add_fan_out_edges(dispatch, branches)     # verified
    builder.add_fan_in_edges(branches, merge)         # verified
    return builder.build()


def viz(cfg: dict) -> str:
    """WorkflowViz.to_mermaid on the REAL built graph — the ARCHITECTURE.md
    Mermaid can drift; this cannot."""
    return WorkflowViz(build(cfg, CostLedger("viz"))).to_mermaid()
