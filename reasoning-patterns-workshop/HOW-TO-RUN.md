# How to Run Any Pattern

Every pattern folder under `patterns/` is standalone, but they all share the
same mechanics: the same six `make` targets, the same variant system, the
same way of reading traces, the same cost-reporting story. This doc covers
all of that ONCE. Each pattern's own README only covers what's distinctive
about it — its scenario, its eval rows, its exhibits — and links back here
for everything else.

## The six standard targets

Defined once in `patterns/common.mk`, included by every pattern's Makefile
(`make help` inside any pattern folder lists all of its targets, including
any pattern-specific ones beyond these six):

```bash
make deploy       # provision/register whatever this pattern needs (agents, infra)
make run          # execute the pattern once against data/sample_input.json
make eval         # score the full data/eval_dataset.jsonl in Foundry Evaluations
make eval-smoke   # same, but only the first 2 rows — a fast sanity check
make cost         # print the cost table across every run so far (see below)
make destroy      # remove THIS pattern's resources; shared infra stays
```

**Prerequisite for all of them:** module 0 has run once —
`cd infra/shared && ./deploy.sh` — which provisions the shared Foundry
project, model deployments, AI Search, storage, and the MCP server, and
writes `.shared-env` at the repo root. Every pattern reads that file; none of
them provision their own copy of the shared infrastructure.

## Variants

A pattern's `variants/` directory holds one YAML file per configuration —
`baseline.yaml` is always the default. Switch with an environment variable,
no code changes:

```bash
make run                        # baseline
VARIANT=cheap-model make run    # whatever variant that pattern defines
make eval VARIANT=cheap-model   # same switch, for evaluation
```

Common variant shapes across patterns: a `single-frontier`/`single_call`
falsifiability baseline (one plain model call, no orchestration — the
question every pattern should be able to answer is "does the orchestration
actually beat this?"), ablations that disable one mechanism to show what it
was worth (a knowledge base, a pruning step, a review gate), and — on
patterns 02/03/05/08 — a `steerable` variant (see below).

## Reading results

**Traces.** Every pattern calls `reasoning_common.telemetry.init()` with a
per-pattern service name (named in that pattern's own README). Find spans in
Azure App Insights → Transaction search → filter by that service name, or by
the `run_tag` attribute a run prints. Each pattern's README tells you the
specific span sequence a healthy run produces — that's the part worth
reading per-pattern; the navigation itself is what's covered here.

**Foundry portal.** Agents registered by `make deploy` are visible under your
project → Agents, including a "try in playground" option where one exists.
Evaluation runs land under project → Evaluations (the "Experiments" table);
each `make eval` run is named `<pattern>-<variant>-<timestamp>` and tagged
with both, so selecting two rows and comparing is how you see what changing
a variant actually did to the scores.

**Cost.** `make cost` reads every `runs/cost-*.json` a pattern has produced
and prints a table. The dollar figures are **illustrative by default** —
invented to demonstrate the ratio between model tiers (§18's worked
example), never real billing data — and the table says so loudly if any row
is illustrative. To use real prices, set `REASONING_WORKSHOP_PRICES` to a
JSON file of `{"deployment": [input_$/1M, output_$/1M]}`, or pass
`prices_path=` to `CostLedger` directly. See `common/reasoning_common/costs.py`.

## Steering (patterns 02, 03, 05, 08)

Four patterns support pausing a run for human input at a **typed contract
boundary** — never mid-generation, which is what keeps every intervention
loggable and resumable:

```bash
make run-interactive        # (VARIANT=steerable under the hood)
```

The specific boundary differs per pattern (a tool approval in 02, a plan/
review checkpoint in 03, a prune decision in 05, an exception review in 08)
— each README covers its own. Two things are universal:

- **Config alone never implies interactivity.** `--interactive` at the call
  site is what turns on a human gate; `make eval VARIANT=steerable` stays
  headless and uses a deterministic stand-in decision-maker, so evaluating a
  steerable variant never blocks waiting for a human who isn't there.
- **The wall-clock budget pauses while a human thinks** (`human_wait_s` in
  the budget snapshot) — model calls and tokens still count, but deliberation
  time isn't charged against the reasoning budget.

## Testing without spending anything

Two repo-wide scripts (documented in the root README) work against every
pattern without touching a live Foundry endpoint:

```bash
python3 scripts/verify_offline.py   # pure logic: budgets, contracts, evaluators, ...
python3 scripts/run_ci_smoke.py     # drives every pattern's run_case() end-to-end, ~3s
```

Neither needs `.shared-env`, Azure credentials, or network access — the
second uses `reasoning_common.fake_backend` to synthesize structurally valid
(not semantically evaluated) responses, so it proves the control flow works,
not that a real model would produce a good answer.

## Tear down

```bash
make destroy       # this pattern only
```

Everything is tagged `workshop=reasoning-patterns` — find anything orphaned
with `az resource list --tag workshop=reasoning-patterns -o table`. To remove
the shared infrastructure itself: `infra/shared/destroy.sh` (this deletes
the resource group and affects every pattern).
