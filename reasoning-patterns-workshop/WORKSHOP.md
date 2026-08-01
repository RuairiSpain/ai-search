# Workshop Guide

## Audience & outcome
Cloud Solution Architects and senior engineers. Outcome: you can select a reasoning
pattern for a customer problem, deploy it on Foundry, *measure* whether it beats a single
frontier call, and demonstrate the frontier→cheap model migration with evidence.

## Prerequisites

| Requirement | Detail |
|---|---|
| Azure subscription | Contributor + User Access Administrator on a resource group |
| RBAC roles | `Azure AI User` on the Foundry project (for you AND each agent's managed identity); `Search Index Data Contributor` on AI Search; `Storage Blob Data Contributor` |
| Model quota (region: check availability first) | `gpt-5.1` (or current frontier), `gpt-5-mini`, `gpt-5-nano`, `claude-haiku-4-5` (reviewer — cross-family), `model-router` |
| Tools | az ≥ 2.67, Python 3.11+ in a venv, make (Docker NOT required — az acr build compiles the MCP image in the cloud) |
| Budget | Full workshop with real evals ≈ $10–25 of model spend. Cost report per pattern: `make cost` |

## Suggested sequence (half-day)

| Time | Module | Teaching point |
|---|---|---|
| 0:00 | Module 0: shared infra + tour of Foundry portal | Project, model catalogue, deployments |
| 0:30 | 01-deliberate-reasoning | Evaluator is the architecture; baseline vs pattern; Agent Optimizer |
| 1:30 | 02-react-tool-loop | Tools + knowledge; reading the Activity tab; injection failure case; budgets |
| 2:30 | 03-multi-agent-routing | Roles ≠ same model; model router; the cost report is the punchline |
| 3:30 | 04-neuro-symbolic | Tendency vs guarantee; decisions cite rules; safety evaluators |
| 4:15 | Wrap: when patterns fail (§20); retire-the-pattern discussion | Keep the baseline forever |

### Optional second half-day A (phase 2 patterns)

| Time | Module | Teaching point |
|---|---|---|
| 0:00 | 05-branching-hypotheses | Delayed commitment; partial truths; pruning economics; steer the search |
| 1:00 | 06-memory-augmented | Memory is a policy layer, not a context window; security trimming is not optional |
| 2:00 | 07-reflection-skills | Improvement between runs; self-modification without a gate is drift |
| 3:00 | 08-workflow-state-hitl | Don't replace the state machine; humans as reasoning participants |

### Optional second half-day B (phase 3 patterns)

| Time | Module | Teaching point |
|---|---|---|
| 0:00 | 09-search-exploration | Spend breadth cheaply, kill invalid free, deepen top-k; infeasibility rows; Prompt Shields |
| 1:00 | 10-graph-reasoning | Relationship problems aren't document problems; entity-resolution discipline; Fabric IQ target |
| 2:00 | 11-program-synthesis | The strongest evaluator is an executable test; weak-tests + verify-full punchline |
| 3:00 | Cross-pattern: `make cost` tour + release-gate retro | The evaluator is the architecture |

## The three commands that matter everywhere

```bash
make deploy && make run && make eval
```

## Reading results

- **Activity tab** (Foundry portal → your project → Agents → select agent → Activity):
  every run's spans — thoughts, tool calls, observations. Each pattern README tells you the
  exact span shape a healthy run produces.
- **Experiments** (portal → Evaluations): each `make eval` logs a run named
  `<pattern>-<variant>-<timestamp>`. Select two runs → **Compare** to see per-row score
  deltas after you change instructions, skills or models.
- **Cost**: `make cost` prints tokens & $ per run per model from traces.

## Teardown
Per pattern: `make destroy`. Everything: `infra/shared/destroy.sh` (deletes the RG).
All resources are tagged `workshop=reasoning-patterns` and `pattern=<name>` — find
strays with: `az resource list --tag workshop=reasoning-patterns -o table`.
