# Troubleshooting

**Quota / model not available in region.** Most common failure. Check
`az cognitiveservices usage list -l <region>` and the model catalogue's region column.
Fix: change `location` in `infra/shared/main.parameters.json`, or swap the model name in
the pattern's `variants/*.yaml` for one you have quota for. Everything model-specific is
in variants files — nothing is hardcoded in Python.

**403 on first agent run.** Managed identity role assignments propagate slowly (up to
~10 min). Wait, or re-run `make deploy` (role assignment steps are idempotent).

**`azd provision` fails with soft-deleted Cognitive Services name collision.**
`az cognitiveservices account purge -l <region> -g <rg> -n <name>` or change
`resourcePrefix`.

**MCP tool calls fail from hosted agent.** The Container App ingress must be reachable
from Foundry. Confirm `curl https://<mcp-fqdn>/health` returns `ok`, and that the agent's
tool definition URL matches `.shared-env` `MCP_SERVER_URL`. If you redeployed shared infra,
re-run the pattern's `make deploy` so agents pick up the new URL.

**Evaluation run shows 0 rows.** The dataset upload requires the project managed identity
to have Storage Blob Data Contributor. Check `scripts/check_roles.sh`.

**Agent Optimizer / model router not visible.** Both have rolled out progressively and may
be preview-gated in your tenant. The README of pattern 01 includes a manual
optimize-with-eval loop as fallback.

**Continuous evaluation shows nothing in App Insights.** Requires the project managed
identity to hold `Azure AI User` on the project *and* the App Insights connection made in
the portal (Tracing blade). Allow ~5 min ingestion delay.

**SDK signature drift.** All Foundry/MAF calls go through
`common/reasoning_common/foundry_client.py`. If Microsoft ships a breaking change, fix it
there once; patterns import only the adapter.

**Evaluation runs fail or don't appear.** Evals use the OpenAI evals API
surfaced by the project (`openai==2.47.0` `client.evals` — SDK shape verified;
graders are `score_model` built in `reasoning_common.foundry_client.score_grader`).
If your project's API version rejects `score_model`, switch the grader type to
`label_model` in that one function — the rubric text carries over unchanged.

**Agent deployed WITHOUT knowledge base (deploy printed a WARN).** Attaching
an index requires a project *connection* to the Search service: portal →
project → Connected resources → Add → Azure AI Search. Copy the connection's
id and add `SEARCH_CONNECTION_ID=<id>` to `.shared-env`, then re-run the
pattern's `make deploy`. Until then the agent runs MCP-only (functionally the
no-knowledge variant). `knowledge_tool()` in
`common/reasoning_common/foundry_client.py` is the single place to adjust.

**pip refuses to install (externally-managed-environment, PEP 668).** Every
`make deploy` tries a normal install, then `--user`; on a fully PEP-668-locked
system BOTH fail, and `scripts/lib.sh`'s `install_shared_reqs` will say so in
one message rather than dumping pip's wall of text twice. Fix: `python3 -m
venv .venv && source .venv/bin/activate`, then re-run `make deploy`.

**MCP server is open to the internet.** By design for the workshop (synthetic
data, tagged short-lived infra) — to lock it down set an `MCP_API_KEY` env var
on the Container App and add the matching `x-api-key` header to the agent's MCP
tool definition; or front it with APIM. Say this to customers before they copy
the pattern.


**Phase-3 patterns can't find their MCP tools (catalog/graph checks fail on
deploy).** Phase 3 added route modules to the shared MCP server; if you
deployed module 0 before phase 3 landed, re-run `infra/shared/deploy.sh` once
to rebuild and redeploy the image (idempotent).

**Prompt Shields calls return checked=false.** The shield endpoint is the
Foundry account's Content Safety surface
(`{account}/contentsafety/text:shieldPrompt`); your identity needs a Cognitive
Services user role on the account, and some regions gate the api-version.
`reasoning_common/safety.py` fails OPEN by design (verdict logged either way)
— the workshop discussion is where you'd flip that to fail-closed.

**Pattern 06: vector store creation fails / semantic memory unavailable.**
`files.upload_and_poll` + `vector_stores.create_and_poll` are verified against
azure-ai-agents 1.1.0, but file-search availability varies by region and
tenant. The code WARNs and falls back to episodic-only rather than failing the
run — check the trace for the warning, and confirm your project supports the
file search tool before blaming the pattern.

**Pattern 06: `TableServiceClient` 403 on first run.** Episodic memory uses the
shared storage account's Table service with AAD (shared keys are disabled
repo-wide). `make deploy` grants you `Storage Table Data Contributor`; allow
up to ~10 minutes for propagation, or re-run the deploy.

**Pattern 07 mutates its own repo.** The skill library under
`patterns/07-reflection-skills/skill_library/` is written to by design.
`make reset-library` restores the preloaded baseline; commit or stash before
demos if you want a clean diff.

**Pattern 08 durable mode: `func` not found.** `deploy-durable.sh` needs Azure
Functions Core Tools v4 (`npm i -g azure-functions-core-tools@4`). Local mode
(`make deploy` / `make run`) needs none of it and is what the evals use.

**Pattern 08: orchestrator replays produce duplicate side effects.** That means
something side-effecting leaked into orchestrator code. The rule is absolute:
orchestrator sequences and waits; activities do everything else (no model
calls, no I/O, no `datetime.now`). `functions_app/function_app.py` is the
reference shape.


**`pip install -r requirements.txt` fails with ResolutionImpossible on
opentelemetry.** Fixed in the pins, but worth knowing why: `agent-framework-core`
allows `opentelemetry-api>=1.39,<2` while `azure-monitor-opentelemetry` pins
`opentelemetry-sdk~=1.43.0`, which transitively caps the api package. The shared
requirements pin **1.43.0** deliberately — do not "helpfully" bump it to the
latest, or every pattern's `make deploy` breaks at the pip step. Run
`python3 scripts/verify_offline.py` after any dependency change.

**Durable Functions deps.** `patterns/08-workflow-state-hitl/functions_app/requirements.txt`
pins `azure-functions==1.24.0` with `azure-functions-durable==1.6.0`. That pair
is what a clean resolve produces and what the API surface was verified against;
`azure-functions` 2.x exists but is untested here.


**A run reports "requires tool approval but no on_tool_approval handler was
supplied".** Expected and deliberate: an approval-gated agent parked and nobody
was there to decide, so the run is cancelled immediately instead of burning the
wall-clock budget and failing with an opaque timeout. Either pass a handler, run
`make run-interactive`, or pick a variant whose tools aren't approval-gated.

**Steerable variants and evaluations.** Interactivity is a property of the call
site, not of a variant file: `select_approver` / `select_steer` only return a
CLI hook when `--interactive` is passed AND stdin is a TTY. `make eval
VARIANT=steerable` therefore runs headless with a deterministic stand-in, and
`scripts/verify_offline.py` enforces this for every variant of every pattern.
