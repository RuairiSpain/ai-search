# Tier 1 — Prompt Agents

The no-code contract. See `00-tier-model-and-concepts.md` for the
escalation table that decides whether an app belongs here.

## What tier 1 actually is

Prompt agents are **durable, versioned definitions with ephemeral
execution**. Both halves matter:

- **Durable.** The agent is a first-class project resource.
  `create_version()` returns an `agent.version` object with `id` and
  `version`; a version selector routes traffic; CI/CD promotes one
  immutable version through dev → test → prod. Nothing about the
  definition is ephemeral.
- **Ephemeral.** There is no container, no sandbox, no `$HOME`, no session.
  Each response is a stateless model-plus-tools invocation on Foundry's
  managed inference plane. Nothing survives between responses except what
  is written to a conversation or to memory.

Durable state for tier 1 lives in **conversations** (stored in Foundry,
single-tenant, optionally backed by your own Cosmos DB) and **memory**
(procedural, user, session — preview, see D2).

**Gateway consequences — three things tier 1 does *not* have:**

| Missing | Consequence |
|---|---|
| `agent_session_id` | nothing to pin; `UpstreamRef.session_id` stays `None` |
| `x-ms-user-identity` delegation | applies per D1, but "endpoint-scoped data" isn't proven to cover conversations — verify with T1-ISO-1 |
| Session Files API | artifacts come from **code interpreter container files**, not a session store — see `07-artifacts-and-code-interpreter.md` |

The isolation unit for tier 1 is therefore the **conversation ID**, and
because every call carries the gateway's managed identity, conversations
created by the gateway belong to the gateway. Per-user mapping is
`gw_context` (`principal_subject` → `conversation_id`) and nothing else.
There is no platform backstop — `gw_context` **is** the security boundary
here (see D1's IDOR control).

## Source of truth: YAML in git, applied by SDK

`azure.yaml` is the declarative schema, but azd's agent extension is still
preview and centred on hosted agents; community reports say prompt-only and
workflow agents aren't fully covered yet. Microsoft's stated current
practice is to keep agents as YAML/JSON in source control and apply them
through CI/CD via SDK or REST.

**So: author the YAML, apply with `create_version()`.** When azd closes the
gap, the same files deploy unchanged.

## Complete agent definition — `agents/writer.yaml`

Everything an agent author needs to know, in one file.

```yaml
# yaml-language-server: $schema=https://raw.githubusercontent.com/Azure/azure-dev/main/schemas/v1.0/azure.yaml.json
kind: prompt
name: writer
displayName: Document Writer
description: Drafts long-form documents from research findings.

model:
  deployment: gpt-5.4-mini        # -> azure.ai.project.deployments entry
  options:
    temperature: 0.4
    topP: 0.95

# Long instructions live beside this file and are inlined at build time.
# Keep them in markdown so writers can review them in a PR.
instructionsFile: ./writer.instructions.md

skills:                            # azure.ai.skill services: shared guidelines
  - house-style
  - citation-rules

toolboxes:                         # azure.ai.toolbox services
  - research-tools                 # azure_ai_search + code_interpreter
  - fabric-knowledge                # Fabric Data Agent via connection

memory:                            # preview — verify against your API version
  kinds: [session, user]
  scope: per-user

outputSchema:                      # structured output, enforced
  properties:
    title:    { type: string, required: true }
    body:     { type: string, required: true }
    sources:  { type: array,  required: false }

# Artifacts: tier 1 has no persistent filesystem. Anything durable must be
# copied out by the gateway (see 07-artifacts-and-code-interpreter.md) or
# written through a tool to a connection-backed store. If this agent needs
# artifacts that outlive one turn AND the gateway isn't harvesting them,
# that is the signal to promote it to tier 2.
```

Supporting services in `azure.yaml`, shared across every agent:

```yaml
services:
  ai-project:
    host: azure.ai.project
    deployments:
      - name: gpt-5.4-mini
        model: { format: OpenAI, name: gpt-5.4-mini, version: "2026-03-17" }
        sku:   { name: GlobalStandard, capacity: 50 }

  fabric-conn:
    host: azure.ai.connection
    uses: [ai-project]
    category: RemoteTool
    target: https://api.fabric.microsoft.com/...
    authType: AAD                  # identity passthrough; end-user credentials

  house-style:
    host: azure.ai.skill
    uses: [ai-project]

  research-tools:
    host: azure.ai.toolbox
    uses: [ai-project, search-conn]
    tools:
      - type: azure_ai_search
        connection: search-conn
      - type: code_interpreter

  writer:
    host: azure.ai.agent
    uses: [ai-project, fabric-conn, research-tools, house-style]
    $ref: ./agents/writer.yaml
```

Two substitution syntaxes: `${VAR}` resolves client-side from
`.azure/<env>/.env` at provision or deploy; `${{ ... }}` is passed through
untouched and resolved server-side by Foundry at runtime. Don't mix them
up — secrets belong in the first form, per-request values in the second.

## Tier 1 → tier 1: workflows

Workflows are declarative multi-agent orchestration in the portal with a
round-trippable YAML view, built on Agent Framework, with sequential,
human-in-the-loop and group-chat templates. Logic is Power Fx (`=` prefix).

```yaml
kind: workflow
trigger:
  kind: OnConversationStart
  id: trigger_wf
actions:
  - kind: SetVariable
    id: init_latest
    variable: Local.LatestMessage
    value: =UserMessage(System.LastMessageText)

  - kind: InvokeAzureAgent
    id: invoke_writer
    agent:
      name: writer
    conversationId: =System.ConversationId
    input:
      messages: =Local.LatestMessage
    output:
      messages: Local.Draft
    autoSend: false                # hold output; the reviewer speaks next

  - kind: InvokeAzureAgent
    id: invoke_reviewer
    agent:
      name: reviewer
    conversationId: =System.ConversationId
    input:
      messages: =Local.Draft
    output:
      messages: Local.Review
    autoSend: false

  - kind: ConditionGroup
    id: gate_on_review
    conditions:
      - id: needs_rework
        condition: =!IsBlank(Find("[REWORK]", Upper(Last(Local.Review).Text)))
        actions:
          - kind: GoTo
            id: retry
            target: invoke_writer   # bounded loop — see the cap below
    elseActions:
      - kind: SendActivity
        id: emit
        activity: =Last(Local.Draft).Text
```

That is the **executor–reviewer** pattern with tier-1 agents only. Notes:

- `autoSend: false` keeps intermediate output off the user's screen.
- Always cap the loop. Group-chat and retry patterns without a round limit
  burn tokens indefinitely.
- Cost scales linearly with agents: a four-agent pipeline costs roughly
  four times a single agent. Use a cheaper deployment for specialists, or
  let the model router pick per request.

**Choosing the mechanism** — this is a semantic choice, not a style one:

| Need | Mechanism | Who owns the user afterwards |
|---|---|---|
| Executor–reviewer, A calls B and keeps control | A2A tool | A |
| Full transfer to a specialist | Workflow / handoff | B; A is out of the loop |
| Repeatable multi-step process with branching | Workflow | the workflow |

Handoff also excludes tool-call contents from the context broadcast to
participants — design around it rather than discovering it in test.

⚠ Mid-run steering inside a workflow (re-reading appended conversation
items before each node) is inferred from the YAML shape, not documented —
spike before promising it. Full detail in D7 (`02-decisions.md`).

## Code interpreter on tier 1

Code Interpreter is GA and attaches directly to a `kind: prompt`
definition:

```json
{
  "name": "chart-agent",
  "definition": {
    "kind": "prompt",
    "model": "<MODEL_DEPLOYMENT>",
    "instructions": "You are a data visualization assistant. When asked to create charts, write and run Python code using matplotlib to generate them.",
    "tools": [
      { "type": "code_interpreter",
        "container": { "type": "auto", "file_ids": ["<FILE_ID>"] } }
    ]
  }
}
```

MCP is also GA, so a two-tool (search + code interpreter) agent is
entirely tier 1. Full container lifecycle, the MCP→code-interpreter
handoff subtlety, and the harvest pattern that copies output to blob
before the container expires: see
`07-artifacts-and-code-interpreter.md`.
