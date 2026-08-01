# T1 sample 08 — executor/reviewer, reviewer has no history

| | |
|---|---|
| Tier | **T1** (two prompt agents) — not fronted by this gateway |
| Stability | **Preview.** Workflows are a preview service (`docs/02-decisions.md` D10 — blocked under `preview: deny`); the two prompt agents underneath are GA |
| RBAC | Same as sample 01 — `Azure AI Developer` on the project to deploy |
| Region features | Both agents' model deployments must be available in-region (D9) |

## What this shows

The **executor–reviewer** pattern from `docs/04-tier1-prompt-agents.md`
("Tier 1 → tier 1: workflows"), with everything — both agents' prompts,
the workflow shape — defined in markdown/YAML, no application code. The
executor drafts a response to a support ticket; the reviewer checks it
against a rubric and either approves it or sends it back for rework.

**The one thing this sample exists to demonstrate:** the reviewer sees
**only the draft it's asked to review** — no conversation history, no
memory of the executor's reasoning, no visibility into anything the
executor saw along the way (tool calls, earlier drafts if this is a retry).
Contrast directly with `../03-code-interpreter-shared-memory`, which is the
same two-agent shape but deliberately gives its second agent the *opposite*
— full shared history and memory.

## Why isolate the reviewer

A reviewer that can see the executor's own chain of reasoning tends to
rubber-stamp it — it's reviewing "did the executor follow its own logic"
instead of "is this draft actually good," which defeats the point of having
a second agent at all. Forcing the reviewer to judge the draft cold, the
same way a human editor who only sees the submitted copy would, is the
actual value of a two-agent pattern here. This is a design choice this
sample takes a stance on, not a platform limitation — nothing stops you
from sharing history if that's what a given pipeline needs (again, see
`../03-code-interpreter-shared-memory`).

## How isolation is achieved

`docs/04`'s own workflow example passes `conversationId:
=System.ConversationId` to *both* `InvokeAzureAgent` steps — same
conversation for executor and reviewer. This sample's
`workflows/review-loop.workflow.yaml` deliberately does **not** do that:
the `invoke_reviewer` step omits `conversationId` entirely, so the platform
opens a fresh, empty conversation for that call. The only data that reaches
the reviewer is whatever's in `input.messages` — here, just the draft text.
`agents/reviewer.yaml` also carries no `memory` block, so there's no
memory-service channel for anything to leak through either.

```
executor.yaml  ──conversation A──▶  "draft"
                                        │
                                        │ input.messages = Local.Draft only
                                        ▼
reviewer.yaml  ──fresh conversation──▶  "approve" | "[REWORK] <reason>"
```

## Structure

```
08-workflow-executor-reviewer/
├── azure.yaml
├── agents/
│   ├── executor.yaml / executor.instructions.md
│   └── reviewer.yaml / reviewer.instructions.md
├── workflows/
│   └── review-loop.workflow.yaml    # bounded retry loop, docs/04 pattern
├── scripts/
│   ├── deploy.py
│   ├── run.py           # direct-SDK driver -- see note below
│   └── teardown.py
└── requirements.txt
```

## Run it

```bash
export FOUNDRY_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
export FOUNDRY_MODEL_DEPLOYMENT=<your deployment name>
az login

pip install -r requirements.txt
python scripts/deploy.py
python scripts/run.py "Ticket: customer wants a refund for a damaged item, order #A-4471."
python scripts/teardown.py
```

`scripts/run.py` drives the pattern directly against the Responses API
rather than invoking the workflow engine, and is the reference for what
"no history" actually means at the wire level: it opens conversation A for
the executor, then opens a **second, brand-new conversation** for the
reviewer and sends it nothing but the executor's draft text. This is
deliberate, not a shortcut — `docs/04` itself flags workflow steering as
"inferred from the YAML shape, not documented — spike before promising it"
(D7), so a sample whose whole point is proving an isolation property is
more trustworthy driven directly against the documented Responses API than
against an unverified workflow-engine behavior. `review-loop.workflow.yaml`
ships alongside it as the declarative equivalent once you've spiked that
part for your project.

## The deliberate failure path

`scripts/run.py --max-rounds 1` caps the rework loop at one round and
prints what happens when the reviewer still isn't satisfied after the cap:
the loop stops and returns the reviewer's last `[REWORK]` verdict as a
`needs_input`-shaped result instead of silently looping forever or
silently shipping an unapproved draft. `docs/04`'s own note — "Always cap
the loop. Group-chat and retry patterns without a round limit burn tokens
indefinitely" — is the thing this failure path exists to make concrete.
