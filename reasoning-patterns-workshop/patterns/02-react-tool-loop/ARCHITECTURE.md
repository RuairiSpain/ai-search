# Pattern 02 — ReAct Tool Loop (§6)

**Scenario:** renewals copilot. Thought → Action (MCP tool) → Observation → …
→ draft offer routed to a human. The **platform owns the loop** (Foundry prompt
agent, §6 runtime row 1); we own instructions, tools, knowledge and guardrails.

```mermaid
flowchart TD
    U[User: assess renewal risk for ACME-001] --> A[renewals-copilot<br/>hosted agent, deployment: small]
    A -->|MCP: get_account| M[(MCP server<br/>Container Apps)]
    A -->|MCP: list_tickets| M
    A -->|MCP: get_contract_terms| M
    A -->|knowledge: contoso-policies| K[(AI Search index<br/>CP-12 / CP-19)]
    A -->|MCP: draft_offer — REVERSIBLE WRITE| M
    M --> H{{Human approval<br/>draft never commits}}
```

Guardrails demonstrated: least-privilege tool allowlist (the agent gets exactly
4 tools, `draft_offer` is the only write and it is reversible-by-design);
observation hygiene (ticket TCK-9007 carries a prompt injection — the eval
checks it's treated as data); loop bounds (run timeout in `budgets.yaml`).

**State sharing shown here:** Agent Service **thread state** — the conversation
thread carries observations between loop steps; nothing custom to build.
Contrast with pattern 01 (in-process state) and 03 (typed contracts).
