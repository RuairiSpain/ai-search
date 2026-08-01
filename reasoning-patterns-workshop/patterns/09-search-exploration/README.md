# Pattern 09 — Search-Based Reasoning & Controlled Exploration (white paper §7)

"What should we migrate first?" is planning, not retrieval: the answer is one
point in a space of valid sequences. This folder makes §7's resource-allocation
idea executable — **breadth cheap, invalid dies free, depth only on the top-k**
— and proves it with the cost report.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy
```

Verifies the phase-3 MCP routes are live (if the catalog check fails, re-run
`infra/shared/deploy.sh` once to rebuild the MCP image — phase 3 added routes).

## 2. Run

```bash
make run
```

Watch the funnel in the output: 8 generated → N killed free by the
deterministic constraint check (`trace.rejected` names each violation) →
survivors scored on `nano` → top-3 deep-analysed → recommendation with
rejected alternatives *and their risk scores*.

## 3. The experiment

```bash
make eval                        # the disciplined search
make eval VARIANT=no-precheck    # every candidate gets a model score
make eval VARIANT=wide           # 16 candidates — does breadth change the answer?
make eval VARIANT=single-frontier
make cost
```

What to put on the projector:

- **baseline vs no-precheck**: same rows, same answers, materially different
  cost — and check whether no-precheck's scorer ever rated an *invalid*
  sequence highly. That's §7's core warning (search amplifies the evaluator)
  caught in your own Experiments table.
- **Rows p09-02 and p09-03 are infeasibility tests**: the constraint set
  cannot be satisfied, and the correct behaviour is to say so with the
  arithmetic. Watch which variants invent a plan anyway — usually the
  single-frontier baseline.
- **Row p09-05** is the injection row (planted instruction in catalog notes);
  the run also passes observations through **Prompt Shields**
  (`reasoning_common/safety.py`) and logs the verdict in the trace —
  defence-in-depth: detector + instructions + eval, and the README question
  for the group: where would you make the shield fail-closed?

## 4. Traces

App Insights → service `pattern-09-search`. Spans: `p09.generate` →
`p09.constrain` (attribute `precheck`) → `p09.score` (attribute `survivors`) →
`p09.deepen` (attribute `k`). The funnel narrowing across those attributes IS
the pattern; screenshot it.

## 5. Change things

Edit `check_sequence()` in `src/workflow.py` — add "S3 must retire by wave 3"
— and re-eval: behaviour changes, no prompt changed, and the rule is a git
diff (the §14 discipline reappearing inside a §7 pattern).
