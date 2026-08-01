# Pattern 01 — Deliberate Reasoning (white paper §5)

**Problem class:** the system has the right information but commits to the first
plausible answer. **Fix:** generate candidates, evaluate (rules first, judge
second), select — and always compare against a single-frontier-call baseline.

Diagram and design notes: [ARCHITECTURE.md](ARCHITECTURE.md). Healthy-run
reference: [data/expected_output.md](data/expected_output.md).

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

Prerequisite: module 0 (`infra/shared/deploy.sh`) has run and `.shared-env`
exists at the repo root.

```bash
make deploy
```

This installs Python deps, registers the **playground agent**
(`p01-deliberate-playground`, a declarative prompt agent so you can poke the
generator solo), and builds/registers the **hosted agent** container for the
full custom loop. If the hosted-agent CLI isn't enabled in your tenant yet,
the script tells you the one portal step to do instead — local runs work
regardless.

## 2. Run the pattern

```bash
make run                        # baseline variant
VARIANT=single-frontier make run   # the falsifiability baseline
```

You'll see the selected hypothesis, alternatives considered, anything rejected
by the RB-7 compliance check, the budget snapshot, and per-deployment cost.

## 3. Use it in the Foundry playground

Portal → your project → **Agents** → `p01-deliberate-playground` → **Try in
playground**. Paste the query from `data/sample_input.json`. You are talking to
*one generator call* — notice it happily returns a single hypothesis with no
comparison. That's the point: the deliberation lives in the orchestration, not
the model. Now run `make run` and compare the outputs side by side with the
group.

## 4. Read the traces — Activity tab

Portal → project → **Agents** → select the agent → **Activity** (local runs
also appear under Application Insights → Transaction search, service
`pattern-01-deliberate`). A healthy run shows:

- one `deliberate.generate` span with `n=3` and the variant name as attributes,
- three generator calls, zero-to-one rejected before judging,
- judge calls on the `nano` deployment (cheaper than the generator — check the
  model attribute),
- no `deliberate.escalate` span. If you see one, read its `reason` attribute:
  that's the budget guardrail firing (try lowering `max_llm_calls` in
  `budgets.yaml` to 2 to trigger it deliberately).

## 5. Evaluate, then change something and evaluate again

```bash
make eval                        # baseline variant, all 8 rows
make eval VARIANT=single-frontier
make eval VARIANT=cheap-model
```

Each run: local execution over `data/eval_dataset.jsonl` (including two
deliberate failure cases: an instruction-override attempt and a
blocked-evidence case) → responses uploaded as a versioned dataset → cloud
evaluation with three evaluators (relevance, groundedness, and a custom **RB-7
rubric**).

**Experiments table:** portal → project → **Evaluations**. Each run is named
`01-deliberate-reasoning-<variant>-<timestamp>` and tagged with the variant.
Select the baseline and single-frontier rows → **Compare**: per-row score
deltas appear side by side. Then run `make cost` — the discussion is whether
the quality delta justifies the cost delta. Sometimes it won't. That is the
white paper's closing test working as intended.

**Release gate (do this as a group):** decide a threshold (e.g. RB-7 rubric
mean ≥ 4.0). The cheap-model variant ships only if it clears the gate. This is
§17's "define thresholds as release gates" in miniature.

## 6. Change instructions or skills

Edit `agents/deliberate-diagnostician/instructions.md` or either skill file in
`skills/` — for example, delete the RB-7 constraint from the instructions and
watch the deterministic check start rejecting candidates (the *system* still
holds the line even when the *prompt* forgets: that's why rules live outside
prompts, §14). Re-run `make eval` and compare in Experiments.

## 7. Run the optimizer

Preferred: portal → Agents → `p01-deliberate-playground` → **Optimize** (Agent
Optimizer; preview in some tenants) with the uploaded dataset. Fallback (and
the transparent version of the same loop):

```bash
make optimize     # writes agents/deliberate-diagnostician/instructions.v2.md
git diff          # governance gate: YOU review before it takes effect
make eval VARIANT=improved-instructions
```

Compare the improved-instructions run against baseline in Experiments. Typical
result: the rewrite tightens the evidence-request behaviour on rows p01-04 and
p01-08.

## 8. Tear down

```bash
make destroy      # removes p01-* agents; shared infra stays for other patterns
```

## What this folder demonstrates beyond the pattern

Declarative vs hosted agent runtimes (§6 table rows 1–2) · budgets and
escalation (§18) · deterministic-check-before-judge (§3) · variant-based A/B in
Experiments (§17) · Agent Optimizer with a governance gate (§10).
