# Pattern 03 — Multi-Agent Orchestration & Model Selection (white paper §12)

Different reasoning roles, different models: frontier planner, cheap workers,
a **reviewer from a different model family**, capped debate, and a cost report
that ends the "frontier everywhere" argument with a table instead of an opinion.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy   # creates the p03-state blob container + role assignment
```

The agents here are code-side executors (a **MAF workflow** —
`src/maf_workflow.py`, with a line-equivalent fallback in `src/workflow.py`),
so there's nothing to register in the portal; the pattern shows the
custom-orchestration runtime row of §6.

## 2. Run

```bash
make run
```

Watch the phases print: plan → parallel fan-out (real `get_segment_metrics`
MCP calls made *by code*, contrast with pattern 02's declarative tool
attachment) → review → merge.

**State sharing #3:** open portal → storage account → container `p03-state` →
your run tag. `plan.json`, `fanout-r0.json`, `decision.json` are the typed
contracts checkpointed mid-flight — this is what lets a crashed orchestration
resume and lets an auditor reconstruct a decision.

## 3. Activity / traces

App Insights → Transaction search → service `pattern-03-multiagent`, or filter
by the `run_tag` attribute printed by `make run`. A healthy run: one `p03.plan`
span → one `p03.fanout` (round=0) containing three parallel model calls → one
`p03.review` → one `p03.merge`. If you see `p03.fanout` with `round=1`, click
the preceding review span: the reviewer's `issues` list is the reason. Two
revise rounds is the cap — after that the run *escalates to a human* rather
than looping (§12's debate bound; try it by asking something unanswerable).

## 3b. Run it via REAL Microsoft Agent Framework execution

```bash
VARIANT=maf make run
VARIANT=maf make eval
```

Every other MAF graph in this workshop (`maf_workflow.py` in this pattern and
in pattern 05) exists for study, `WorkflowViz`, or the steering demo — it's
never on the path `make run`/`make eval` actually execute. This variant is
the exception: `engine: maf` in `variants/maf.yaml` routes `run_case`
through `src/maf_workflow.py`'s real `WorkflowBuilder` graph
(`asyncio.run(maf_workflow.run(...))`), and the graph genuinely runs —
planner executor, fan-out to workers, reviewer, merger — end to end,
producing the same `Decision` shape as the dependency-free path.

It calls the *exact same* `plan`/`work`/`review`/`merge` functions this file
defines (`maf_workflow.py` does `import workflow as impl`), so `baseline` and
`maf` should score identically in Experiments — that equivalence is worth
checking, not assuming. What differs is the runtime: hand-rolled control flow
vs. a framework-managed graph with per-executor tracing.

A MAF-executor failure (e.g. the reviewer rejecting) surfaces as a caught
error in the response, not a crash — `make eval VARIANT=maf` still produces
one row per dataset item like every other variant.

## 4. Evaluate — the model-selection experiment

```bash
make eval                          # frontier planner + small workers + claude reviewer
make eval VARIANT=all-frontier     # the naive build
make eval VARIANT=router           # workers on the model-router deployment
make eval VARIANT=same-family-review
make cost
```

In portal → Evaluations, compare the four runs on the decision-hygiene rubric,
then put `make cost` on the projector. The expected story (§12/§18):

- baseline ≈ all-frontier on quality for p03-01..03, at a fraction of the cost;
- p03-04 (close arithmetic) and p03-05 (false premise) are where reviewers earn
  their keep — check whether **same-family-review** waves through errors that
  the cross-family reviewer caught. Correlated blind spots are real and now
  you have eval rows demonstrating them;
- **router** lands between: per-prompt selection without per-role tuning.

## 5. Change things

- Edit `agents/reviewer/instructions.md` to remove the numeric-traceability
  check → re-eval → watch p03-04/05 scores drop while p03-01 stays flat.
  Evals catch what demos don't.
- Lower `max_review_rounds` in `budgets.yaml` to 0 and see escalations appear.
