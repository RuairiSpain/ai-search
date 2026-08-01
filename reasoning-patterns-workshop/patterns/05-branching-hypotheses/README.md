# Pattern 05 — Branching Hypotheses / Tree of Thoughts (white paper §8)

Five hypotheses kept alive at once; evidence discriminates; commitment is
delayed until it does. The seeded scenario has a nuance the eval turns on:
the geo anomaly is REAL travel AND the account is compromised via a
consent-phished OAuth app. Forcing a single winner is the failure.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy   # verifies the identity MCP route (phase-2 addition)
```

If the check fails, re-run `infra/shared/deploy.sh` once — phase 2 added the
identity route to the MCP image.

## 2. Run

```bash
make run
make viz      # WorkflowViz renders the REAL MAF-built graph
```

`make viz` uses **verified** `WorkflowViz.to_mermaid()` on the actual built
workflow — the diagram is generated from code, so it cannot drift from what
runs. Copy the output over the ARCHITECTURE.md diagram if you edit the graph.

## 3. Steer the search

```bash
make run-interactive       # VARIANT=steerable
```

At every prune boundary you see the ranked branches with scores. Options:
continue, `k H3 H5` to kill named branches, `b H3` to boost/protect one from
prune, or `e <reason>` to escalate. This is domain expertise steering the
**search budget** — the most valuable steering there is. Interventions land in
`trace.interventions` and the wall clock pauses while you think (per phase-1
Budget.human_wait).

## 4. The experiments

```bash
make eval                          # baseline
make eval VARIANT=no-pruning       # keep every branch every round
make eval VARIANT=narrow           # top-2 beam — premature commitment risk
make eval VARIANT=single-frontier
make cost
```

- **no-pruning** measures the §8/§20 "over-branching and hidden cost explosion"
  failure mode. Expect ~2x call count for near-identical answers on rows
  01/04, unchanged answer on p05-05 (injection), and possibly LOWER quality
  on the noisy rows because low-scoring branches drag scoring context.
- **narrow (top-2)** is the delayed-commitment demo: on p05-01, the OAuth
  branch (H3) sometimes gets pruned before oauth grants are pulled. When
  that happens, the verdict misses the actual compromise — teaches the cost
  of premature commitment better than any slide.
- **p05-02 and p05-03** test bad-faith premises in the query (scope
  restriction, unverified "pre-approval"); the pattern must not accept them.
- **p05-05** is the injection row (planted instruction in OAuth publisher
  description); Prompt Shields verdict logs in the trace.

## 5. Traces

App Insights → service `pattern-05-branching`. Spans per round:
`p05.expand` (attribute `live`) → `p05.score` → prune decision → next round.
The `live` attribute across rounds IS the search cone narrowing — screenshot it.
