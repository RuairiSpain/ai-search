"""The same graph expressed as a Microsoft Agent Framework workflow.

API-checked against agent-framework==1.12.1 (import-tested; see
scripts/check_package_versions.py output in the repo history):
  - WorkflowBuilder(start_executor=...) — start is a constructor arg
  - @executor(id=...) function executors with WorkflowContext[T]
  - ctx.set_state / ctx.get_state for cross-executor state
  - workflow.run(...) -> WorkflowRunResult; .get_outputs() for yields
Business logic lives ONLY in workflow.py; these executors adapt messages to
the same functions, so the two graphs cannot drift apart.

MAF features deliberately NOT used here (kept hand-rolled in workflow.py as
the teaching exhibit) but recommended for production, see
docs/FOUNDRY-MAF-COVERAGE.md: add_fan_out_edges/add_fan_in_edges,
checkpointing via WorkflowBuilder(checkpoint_storage=...), MCPStreamableHTTPTool.
"""
from __future__ import annotations

import sys
from pathlib import Path

PATTERN_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PATTERN_DIR.parents[1] / "common"))

from agent_framework import (  # type: ignore
    Executor, WorkflowBuilder, WorkflowContext, executor, handler, response_handler,
)

from reasoning_common.budgets import Budget
from reasoning_common.config import load_budgets
from reasoning_common.contracts import Plan, Review, WorkerOutput
from reasoning_common.costs import CostLedger

import workflow as impl  # single source of business logic


def build(cfg: dict, ledger: CostLedger):
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p03-maf")

    @executor(id="planner")
    async def planner(query: str, ctx: WorkflowContext[Plan]) -> None:
        p = impl.plan(query, cfg, budget, ledger)
        ctx.set_state("goal", p.goal)
        await ctx.send_message(p)

    @executor(id="fanout_workers")
    async def fanout(plan: Plan, ctx: WorkflowContext[list[WorkerOutput]]) -> None:
        done: dict[str, WorkerOutput] = {}
        for sub in plan.subtasks:  # see docstring: add_fan_out_edges is the prod shape
            done[sub.id] = impl.work(sub, done, cfg, budget, ledger)
        await ctx.send_message(list(done.values()))

    @executor(id="reviewer")
    async def reviewer(outputs: list[WorkerOutput], ctx: WorkflowContext[list[WorkerOutput]]) -> None:
        goal = ctx.get_state("goal")
        r: Review = impl.review(outputs, goal, cfg, budget, ledger)
        if r.verdict != "approve":
            raise RuntimeError(f"review verdict={r.verdict}: {'; '.join(r.issues)}")
        await ctx.send_message(outputs)

    @executor(id="merger")
    async def merger(outputs: list[WorkerOutput], ctx: WorkflowContext[None, str]) -> None:
        goal = ctx.get_state("goal")
        d = impl.merge(outputs, goal, cfg, budget, ledger)
        await ctx.yield_output(d.model_dump_json(indent=2))

    return (WorkflowBuilder(start_executor=planner)
            .add_edge(planner, fanout)
            .add_edge(fanout, reviewer)
            .add_edge(reviewer, merger)
            .build())


async def run(query: str, cfg: dict, ledger: CostLedger) -> str:
    wf = build(cfg, ledger)
    result = await wf.run(query)
    outputs = result.get_outputs()
    return outputs[0] if outputs else "(no output yielded)"


# --------------------------------------------------------------------------- #
# Steerable variant — MAF-native pause/resume via request_info.
# Signature-verified against agent-framework==1.12.1:
#   ctx.request_info(request_data, response_type)  pauses the workflow;
#   result.get_request_info_events()               surfaces what's being asked;
#   wf.run(responses={request_id: answer})         resumes with the answer.
# Event FIELD access below is duck-typed (getattr) because the event class
# isn't a top-level export — if fields moved, fix _event_id/_event_data only.
# --------------------------------------------------------------------------- #
class SteerablePlanner(Executor):
    """Class-based executor because request_info responses come BACK to the
    requesting executor via @response_handler — verified against
    agent-framework 1.12.1 (`WorkflowContext.request_info` docstring). A
    downstream 'apply the answer' executor does NOT receive them, and wiring
    one fails graph type-validation at build time."""

    def __init__(self, cfg: dict, ledger: CostLedger, budget: Budget, id: str = "planner"):
        super().__init__(id=id)
        self._cfg = cfg
        self._ledger = ledger
        self._budget = budget

    # Explicit types: this module uses `from __future__ import annotations`, so
    # annotations are strings and the decorators' introspection path rejects
    # them. The SDK documents explicit decorator parameters as the escape
    # hatch — when ANY is given, all types must be explicit.
    @handler(input=str, output=Plan)
    async def plan_and_ask(self, query, ctx) -> None:
        p = impl.plan(query, self._cfg, self._budget, self._ledger)
        ctx.set_state("goal", p.goal)
        ctx.set_state("pending_plan", p.model_dump())
        await ctx.request_info(
            {"stage": "plan", "plan": p.model_dump(),
             "question": "Approve this plan? Reply 'ok' or subtask ids to drop."},
            str,
        )

    @response_handler(request=dict, response=str, output=Plan)
    async def apply_steer(self, original_request, response, ctx) -> None:
        p = Plan.model_validate(ctx.get_state("pending_plan"))
        answer = (response or "ok").strip()
        if answer.lower() not in ("", "ok", "y", "yes"):
            drop = set(answer.split())
            keep = [s for s in p.subtasks if s.id not in drop]
            if keep:
                p = p.model_copy(update={"subtasks": keep})
        await ctx.send_message(p)


def build_steerable(cfg: dict, ledger: CostLedger):
    """Same graph, but the planner requests human sign-off on the Plan before
    fan-out. This is the mechanism pattern 08 scales up with Durable external
    events; here it is the minimal MAF-native form."""
    budget = Budget.from_config(load_budgets(PATTERN_DIR), label="p03-maf-steer")
    planner = SteerablePlanner(cfg, ledger, budget)

    @executor(id="fanout_workers")
    async def fanout(plan: Plan, ctx: WorkflowContext[list[WorkerOutput]]) -> None:
        done: dict[str, WorkerOutput] = {}
        for sub in plan.subtasks:
            done[sub.id] = impl.work(sub, done, cfg, budget, ledger)
        await ctx.send_message(list(done.values()))

    @executor(id="reviewer")
    async def reviewer(outputs: list[WorkerOutput], ctx: WorkflowContext[list[WorkerOutput]]) -> None:
        goal = ctx.get_state("goal")
        r: Review = impl.review(outputs, goal, cfg, budget, ledger)
        if r.verdict != "approve":
            raise RuntimeError(f"review verdict={r.verdict}: {'; '.join(r.issues)}")
        await ctx.send_message(outputs)

    @executor(id="merger")
    async def merger(outputs: list[WorkerOutput], ctx: WorkflowContext[None, str]) -> None:
        goal = ctx.get_state("goal")
        d = impl.merge(outputs, goal, cfg, budget, ledger)
        await ctx.yield_output(d.model_dump_json(indent=2))

    return (WorkflowBuilder(start_executor=planner)
            .add_edge(planner, fanout)
            .add_edge(fanout, reviewer)
            .add_edge(reviewer, merger)
            .build())


def _event_id(ev) -> str:
    return getattr(ev, "request_id", None) or getattr(ev, "id", "")


def _event_data(ev):
    return getattr(ev, "data", None) or getattr(ev, "request_data", {})


async def run_steerable(query: str, cfg: dict, ledger: CostLedger, ask=input) -> str:
    """Drive the pause/resume loop: run → surface requests → collect answers →
    resume. `ask` is injectable for tests."""
    wf = build_steerable(cfg, ledger)
    result = await wf.run(query)
    while True:
        requests = result.get_request_info_events()
        if not requests:
            break
        responses = {}
        for ev in requests:
            data = _event_data(ev)
            print(f"\n⏸  MAF request_info: {data.get('question', data)}")
            for s in (data.get("plan", {}) or {}).get("subtasks", []):
                print(f"   [{s['id']}] ({s['kind']}) {s['instruction']}")
            responses[_event_id(ev)] = ask("   > ")
        result = await wf.run(responses=responses)
    outputs = result.get_outputs()
    return outputs[0] if outputs else "(no output yielded)"
