# Samples

Eight worked examples across all three tiers. This is a first slice of the
target library laid out in `docs/02-decisions.md` ("Documentation and
samples structure") — that section lists the full eventual set
(`tier1/01`..`10`, `tier2/01`..`04`, `tier3/01`..`04`, `gateway/01`..`02`);
what exists on disk today is the subset below, plus one addition beyond
that original list (`tier3/05-push-notifications` — see that section's own
note). Numbering matches the target list where a sample corresponds
directly to one of its entries, so filling in the rest later doesn't
require renaming anything.

| Sample | Tier | Demonstrates |
|---|---|---|
| [`tier1/01-basic-declarative-agent`](tier1/01-basic-declarative-agent/) | T1 | A single declarative prompt agent: instructions in markdown, a skill, an MCP toolbox. The "classic Foundry agent" shape. |
| [`tier1/08-workflow-executor-reviewer`](tier1/08-workflow-executor-reviewer/) | T1 | Executor → reviewer, both defined entirely in markdown/YAML. The reviewer runs in an **isolated conversation** — no history, no memory of the executor's turn. |
| [`tier1/03-code-interpreter-shared-memory`](tier1/03-code-interpreter-shared-memory/) | T1 | Code interpreter does a math calculation from the user's prompt; a second agent **shares the same conversation and memory scope** and reformats the result into an ELI5 preschool multiple-choice flashcard. Deliberately the mirror image of the sample above. |
| [`tier2/02-per-user-isolated-storage`](tier2/02-per-user-isolated-storage/) | T2 | Three simulated users hit the same hosted agent. A function tool proves each gets an isolated, persistent `$HOME` (no cross-user leakage); code interpreter writes each user's prompt to a real `.docx`, harvested by the gateway's existing artifact pipeline and returned as a download link that outlives the agent session. |
| [`tier2/04-long-running-hello-world`](tier2/04-long-running-hello-world/) | T2 | One hosted agent, ~5 minutes of work, fronted by this gateway. Narration is automatic and coarse: one line naming the tool call in progress ("running tool: slow_then_greet"), unchanged for the whole run, derived from the platform's own `Response.output` — no agent-side code. |
| [`tier3/01-durable-hello-world-status`](tier3/01-durable-hello-world-status/) | T3 | The same ~5 minute hello world, this time as a durable orchestration that pushes explicit, author-chosen narration at each of five steps via webhook. Same client, visibly finer-grained experience. |
| [`tier3/03-hitl-durable`](tier3/03-hitl-durable/) | T3 | A multi-day expense-approval orchestration that pauses on `wait_for_external_event`, racing it against a deadline timer. The gateway sees the pause as `TASK_STATE_INPUT_REQUIRED`; a client resumes it with a second `SendMessage`, which this sample's own A2A server routes to `client.raise_event(...)`. Deliberate failure path: the deadline wins instead of the approval. |
| [`tier3/05-push-notifications`](tier3/05-push-notifications/) | T3 | `CreateTaskPushNotificationConfig` against a real local receiver — the client never calls `GetTask`, it registers a callback and blocks on its own tiny HTTP server, watching each status update arrive the instant the gateway delivers it. Deliberate failure path: registering a non-allowlisted URL, rejected by `gwlint` rule L023's own runtime check. |

The T2/T3 pair is the point: identical wall-clock behavior, both tiers
narrate, but through genuinely different mechanisms with different
granularity ceilings. T2's narration (docs/05 §5.4) is derived
automatically from standard Responses-API output items — real, verified,
and zero agent effort, but it can only ever describe tool-call boundaries,
never what happens inside one. T3's narration (docs/06 §5.4) is an
explicit `gw.progress.v1` payload the orchestrator's own code chooses to
push via webhook — real per-step control, at the cost of having to write
that code. Both land as the same `StatusEvent.detail` field on the gateway
side and reach the wire through the same `TaskUpdater.update_status(...,
message=...)` call — one rendering path, two different sources.

## Why T1 samples don't touch the gateway at all

T1 agents aren't fronted by this gateway at all (`docs/00` §0, `docs/04`) —
they get Foundry's own native incoming A2A endpoint. So the T1 samples
exercise `azure-ai-projects` directly against a Foundry project, with no
gateway in the loop. Every T2/T3 sample is the opposite: the whole point is
what changes when the gateway *is* in the loop, so all of them ship a
client that talks to a running gateway instance, not to Foundry or the T3
app directly. T3 has three samples for the same reason T1 has three:
`01-durable-hello-world-status`, `03-hitl-durable`, and
`05-push-notifications` each isolate one gateway-facing mechanism
(narration, `input_required`, push delivery) rather than combining all
three into one sample where it would be hard to tell which behavior came
from which mechanism.

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
