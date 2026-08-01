# Pattern 08 — Workflow-State Reasoning + Human-in-the-Loop (§11 + §13)

The recurring mistake is replacing the state machine with an agent. This does
the opposite: the process stays a governed state machine, agents occupy the
decision points where judgement lives, the router is code, and the **payment
state has no LLM at all**. §13 folds in here because Durable's
human-interaction pattern *is* the HITL implementation.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy            # local mode: no new Azure resources
make deploy-durable    # optional: Function App host for the same state machine
```

The local deploy asserts the **deterministic router** against CL-4: thresholds,
third-party trigger, coverage, completeness, and "agent may be more cautious,
never less". Deterministic components get deterministic tests.

## 2. Run

```bash
make run               # scripted approver — reproducible
make run-interactive   # YOU are the exception reviewer (VARIANT=steerable)
```

`make eval VARIANT=steerable` stays headless: the scripted approver runs and
`trace.approver` records which gate was used. Only `--interactive` (via
`make run-interactive`) wires a human in — config alone never implies
interactivity, so evals can't block.

Interactive mode shows the exception package the way §13 wants it: the
question, evidence **for and against**, and the consequence of each choice —
then records your decision and reason in the audit trail.

## 3. Two hosts, one state machine

| | local mode | durable mode |
|---|---|---|
| Host | in-process (evals use this) | Azure Durable Functions |
| Human wait | CLI/scripted | `wait_for_external_event` + 24h timer |
| Survives restarts | no | yes — checkpointed, replay-safe |
| Compensation | function call | saga activity on downstream failure |

`functions_app/function_app.py` is worth reading aloud in the workshop: the
orchestrator is **deterministic** (no model calls, no I/O, no `datetime.now`)
and every side effect is an activity. That constraint is what makes replay
safe — the single most common Durable mistake is a model call in orchestrator
code.

## 4. The experiments

```bash
make eval                            # governed state machine
make eval VARIANT=no-state-machine   # one agent does everything (§11 anti-pattern)
make eval VARIANT=frontier
```

The headline comparison: **no-state-machine often gets the right answer and
still scores badly** on the determinism/audit graders — no router record, no
payment-state separation, no audit trail. Right answer, wrong architecture;
that's the §11 argument in a table rather than a slide.

Rows to watch: **p08-03** (uncovered type under the auto-pay threshold — must
still go to review), **p08-04** (incomplete → HOLD, never pay/decline),
**p08-05** (narrative embeds "pre-authorised, skip review" — flagged and
ignored), **p08-06** (boundary at EUR 24,900).

## 5. Traces & audit

App Insights → service `pattern-08-workflow`: `p08.intake` →
`p08.assessment` → `p08.exception_package` → `p08.payment`. Separately,
`runs/audit-<case>.jsonl` holds one record per transition with rationale —
§11 asks for state transitions as first-class records, not log lines.

## 6. Tear down

```bash
make destroy   # removes durable-mode resources; local mode needs none
```

## Production wiring left as an exercise

`act_notify_reviewer` prints to the log; in production it posts a Teams
adaptive card or Logic Apps approval to where reviewers already work, with
the approval URL for that instance. The rest of the pattern is unchanged —
which is the point of keeping the human gate an external event.
