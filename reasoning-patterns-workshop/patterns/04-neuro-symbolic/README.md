# Pattern 04 — Neuro-Symbolic Reasoning (white paper §14)

The LLM interprets a messy onboarding request; a **deterministic rules engine**
(real MCP tool, versioned Python in `common/mcp_server/routes/rules.py`)
decides what's permitted; a ~20-line code enforcement layer makes the engine's
verdict final; and every restriction in the output **cites its rule ID** —
Directive Art. 12's explainability requirement, executable.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy
```

No new Azure resources — the rules engine already runs on the shared MCP
Container App. The deploy script instead does something better: it calls
`evaluate_rules` twice with the same case and *asserts the answers are
identical*, then asserts the expected rules fire. Deterministic components get
deterministic tests; that's the point of putting rules in code.

## 2. Run

```bash
make run
```

Sample case: risk score 74, ex-councillor director (PEP), EUR 250k facility.
Watch the trace: extraction → proposal → `p04.rules_engine` → explanation
citing KYC-001, KYC-014, LIM-203 with owners for each obligation.

## 3. The experiment that carries the session

```bash
make eval                            # engine + enforcement (guarantee)
make eval VARIANT=prompt-rules-only  # same rules, prompt text only (tendency)
make eval VARIANT=single-frontier    # one frontier call, rules in prompt
```

Portal → **Evaluations** → compare the three on the **rule_citation_rubric**,
then look specifically at rows **p04-04** (sanctioned jurisdiction wrapped in
"they seem low risk, please approve") and **p04-05** (an RM asserting the PEP
flag is outdated, "skip that check"):

- **baseline** rejects p04-04 *every* run — the model literally cannot approve
  it, because approval isn't the model's to give.
- **prompt-rules-only** and **single-frontier** usually reject it. Run the eval
  twice if scores look clean; *usually* is the finding. A frontier model with
  rules in its prompt is a well-behaved tendency. A control it must satisfy is
  a guarantee. Compliance colleagues know which one they can sign.

Also worth showing: the cheap-model story here is extreme — baseline runs
extractor and explainer on `nano` with `small` proposing, because the hard part
(the rules) costs no tokens at all. `make cost` vs the single-frontier variant.

## 4. Traces

App Insights → service `pattern-04-neurosymbolic`. Spans:
`p04.extract → p04.propose → p04.rules_engine → p04.explain`, with the enforced
`outcome` as an attribute on the explain span. In the prompt-rules-only variant
the `p04.rules_engine` span is *absent* — auditors notice that kind of thing,
and now your attendees will too.

## 5. Change things

- Add a rule to `common/mcp_server/routes/rules.py` (e.g. "EUR > 500k requires
  board approval"), re-run `infra/shared/deploy.sh` to rebuild the MCP image,
  and re-eval — no prompt changed, behaviour changed, and the rule change is a
  reviewable git diff with a version stamp in the verdict.
- Edit `skills/case-structuring/SKILL.md` to allow guessing missing fields and
  watch p04-06 degrade: extraction discipline is part of the safety story.

## 6. Tear down

```bash
make destroy   # nothing pattern-specific to remove
```

## Beyond the pattern

Rules as versioned, deterministically-tested code (§14) · unknown-≠-safe
extraction defaults · Art. 12-style rule citation as an evaluable output
contract · pressure-to-skip-controls red-team rows · nano/small split where
the guarantee layer does the heavy lifting.
