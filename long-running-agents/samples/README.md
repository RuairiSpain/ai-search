# Samples

Five worked examples across all three tiers. This is a first slice of the
target library laid out in `docs/02-decisions.md` ("Documentation and
samples structure") — that section lists the full eventual set
(`tier1/01`..`10`, `tier2/01`..`04`, `tier3/01`..`04`, `gateway/01`..`02`);
what exists on disk today is the subset below. Numbering matches that
target list where a sample corresponds directly to one of its entries, so
filling in the rest later doesn't require renaming anything.

| Sample | Tier | Demonstrates |
|---|---|---|
| [`tier1/01-basic-declarative-agent`](tier1/01-basic-declarative-agent/) | T1 | A single declarative prompt agent: instructions in markdown, a skill, an MCP toolbox. The "classic Foundry agent" shape. |
| [`tier1/08-workflow-executor-reviewer`](tier1/08-workflow-executor-reviewer/) | T1 | Executor → reviewer, both defined entirely in markdown/YAML. The reviewer runs in an **isolated conversation** — no history, no memory of the executor's turn. |
| [`tier1/03-code-interpreter-shared-memory`](tier1/03-code-interpreter-shared-memory/) | T1 | Code interpreter does a math calculation from the user's prompt; a second agent **shares the same conversation and memory scope** and reformats the result into an ELI5 preschool multiple-choice flashcard. Deliberately the mirror image of the sample above. |
| [`tier2/04-long-running-hello-world`](tier2/04-long-running-hello-world/) | T2 | One hosted agent, ~5 minutes of work, fronted by this gateway. No `gw.progress.v1` events emitted, so the client only ever sees `submitted` → `working` → `completed` — no narration in between. |
| [`tier3/01-durable-hello-world-status`](tier3/01-durable-hello-world-status/) | T3 | The same ~5 minute hello world, this time as a durable orchestration that pushes `gw.progress.v1` narration at each step via webhook. Same client, visibly different experience. |

The T2/T3 pair is the point: identical wall-clock behavior, and the only
difference visible to a client is whether the agent chose to narrate. That
difference is a property of *how the agent is built*, not of the gateway —
see docs/05 §5.4 and docs/06 §5.4, both of which push the same
`gw.progress.v1` event schema through different transports (T2: filtered
out of the gateway's own poll loop; T3: webhook).

## Why T1 has three samples and T2/T3 have one each

T1 agents aren't fronted by this gateway at all (`docs/00` §0, `docs/04`) —
they get Foundry's own native incoming A2A endpoint. So the T1 samples
exercise `azure-ai-projects` directly against a Foundry project, with no
gateway in the loop. The T2/T3 samples are the opposite: the whole point is
what changes when the gateway *is* in the loop, so both ship a client that
talks to a running gateway instance, not to Foundry directly.

## Prerequisites, all samples

- A Foundry project (`FOUNDRY_PROJECT_ENDPOINT`) with a model deployment.
- `az login` / `DefaultAzureCredential` picking up a principal with rights
  on that project. No account keys anywhere — every sample authenticates
  via `DefaultAzureCredential` and `${VAR}` substitution only, per the
  "requirements every sample must meet" list in `docs/02-decisions.md`.
- Package versions are **not** pinned inline in any sample's
  `requirements.txt`. Each file points at the single pin table in
  `docs/01-gateway-config-and-adapter-contract.md` §3 — that table is
  reviewed monthly precisely so a version doesn't have to be hunted down
  and updated in N places.
- T2/T3 samples additionally need a running gateway (`make dev` from the
  repo root — see the top-level `README.md`) with the sample's app/upstream
  entries added to `config/apps.yaml` (each sample's README shows the
  exact fragment) and `make gwlint` passing against the merged config.

## What every sample includes

Per the requirements list in `docs/02-decisions.md`:

1. A README with a tier/stability header block (tier, preview or GA,
   required RBAC roles, required region features).
2. At least one deliberate failure path, not just the happy path.
3. A teardown step — nothing here is meant to be left running.
4. No secrets, no account keys — `DefaultAzureCredential` and `${VAR}`
   only.

Two items from that list are **not** fully met yet, called out per-sample
rather than silently: "runs from a clean clone with `make run`" (these ship
as documented scripts you invoke directly — no `Makefile` wrapper yet), and
"passes `gwlint` with zero waivers" for the T1 samples specifically, since
`gwlint` only checks *this gateway's* `apps.yaml`/`upstreams.yaml` and T1
apps are never entries there by design (`00-tier-model-and-concepts.md`) —
there is nothing for gwlint to check. The T2/T3 samples' config fragments
do pass gwlint as written.
