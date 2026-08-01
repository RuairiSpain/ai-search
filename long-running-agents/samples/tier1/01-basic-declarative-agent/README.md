# T1 sample 01 — basic declarative agent

| | |
|---|---|
| Tier | **T1** (prompt agent) — not fronted by this gateway, see `docs/00-tier-model-and-concepts.md` |
| Stability | Mixed: prompt agents + MCP + code interpreter are **GA**; the `skills` service used here is **preview** (`docs/02-decisions.md` D10) |
| RBAC | Caller needs `Azure AI Developer` (or equivalent) on the Foundry project to run `create_version()`; the deployed agent runs under its own agent identity |
| Region features | The model deployment (`gpt-5.4-mini` below, or whatever you point `azure.yaml` at) must be available in the target project's region — D9/L001/L002 |

## What this shows

The "classic" Foundry agent: one declarative definition, instructions kept
in a markdown file so non-engineers can review them in a PR, a shared
**skill** (a preview service — a reusable block of guidance attached to
multiple agents), and an **MCP toolbox** for tools. This is the shape
almost every T1 agent should start from — see the escalation table in
`docs/00-tier-model-and-concepts.md` §4: "everything else" defaults here.

Structure follows `docs/04-tier1-prompt-agents.md` exactly — `azure.yaml`
is the source of truth, applied with `agents.create_version()` rather than
the retired `create_agent()` (`docs/01` §3 pin table: `azure-ai-projects
>=2.0.0` is a breaking requirement, not a suggestion).

```
01-basic-declarative-agent/
├── azure.yaml                        # declarative schema — the source of truth
├── agents/
│   ├── concierge.yaml                # kind: prompt, references the .md below
│   └── concierge.instructions.md     # the actual prompt, reviewable in a PR
├── skills/
│   └── house-style.md                # shared guidance, attachable to other agents too
├── toolboxes/
│   └── README.md                     # the MCP toolbox this agent connects to
├── scripts/
│   ├── deploy.py                     # apply azure.yaml -> create_version()
│   ├── chat.py                       # send one turn, print the reply
│   └── teardown.py                   # delete the version + conversation
└── requirements.txt
```

## Run it

```bash
export FOUNDRY_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
az login   # DefaultAzureCredential picks this up; no account keys, ever

pip install -r requirements.txt
python scripts/deploy.py     # creates/updates the agent version
python scripts/chat.py "What's on the house-style checklist for a customer email?"
python scripts/teardown.py   # deletes the version this script created
```

## The deliberate failure path

`scripts/chat.py` sends a prompt containing a request the agent's
instructions explicitly refuse (asking it to fabricate a tracking number —
see `concierge.instructions.md`'s "Refuse, don't guess" section) and prints
both the happy-path reply and this refusal side by side, so you can see the
agent's `outputSchema`-free plain-text refusal versus a normal answer
without needing a second run.

## Why no `outputSchema` / `input-required` here

This agent never needs to pause for clarification mid-turn — it either
answers or refuses in one turn. D4 (`docs/02-decisions.md`) only requires
an `outputSchema` for agents that *can* ask a clarifying question; adding
one here with no `needs_input` path would be dead schema. Sample
`08-workflow-executor-reviewer` and the flashcard formatter in
`03-code-interpreter-shared-memory` both use structured output for a real
reason — see those READMEs.
