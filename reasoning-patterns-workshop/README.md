# Advanced Foundry Agents — Reasoning Design Patterns Workshop

Hands-on companion to **"Reasoning: Thinking Beyond Grounding"**. Each folder under
`patterns/` is a **standalone Azure AI Foundry project** implementing one agentic reasoning
pattern from the guide — hosted agents, real tools (MCP), real knowledge bases, real
evaluations — deployable with one command and torn down with another.

## Phase 1 patterns

| Folder | White-paper § | Pattern | Foundry features showcased |
|---|---|---|---|
| `patterns/01-deliberate-reasoning` | §5 | Generate candidates → evaluate → select | Evaluations, variants A/B, **Agent Optimizer**, baseline comparison |
| `patterns/02-react-tool-loop` | §6 | Thought → Action → Observation loop | Hosted agents, **MCP tools**, **Foundry IQ knowledge base**, tracing/Activity tab, budgets |
| `patterns/03-multi-agent-routing` | §12 | Planner → workers (fan-out) → cross-family reviewer → merger | MAF workflows, **model router**, typed contracts, cost report frontier-vs-routed |
| `patterns/04-neuro-symbolic` | §14 | LLM proposes, rules engine constrains | Azure Functions evaluator/toolset, rule-citing decisions, safety evals |

**Phase 3 (built; slots 05–08 reserved for phase 2):**

| Folder | White-paper § | Pattern | Foundry features showcased |
|---|---|---|---|
| `patterns/09-search-exploration` | §7 | Breadth cheap → free constraint kill → deepen top-k | Budget-allocated search, **Prompt Shields** on observations, cost-of-search ablation |
| `patterns/10-graph-reasoning` | §15 | Model plans traversals over an entity graph | Ontology discipline, chain-cited evidence, docs-only ablation, **Fabric IQ** mapping |
| `patterns/11-program-synthesis` | §16 | Generate → pytest → analyze → repair | Deterministic evaluator (executable tests), tamper guarantees, weak-tests ablation |

**Phase 2 (built):**

| Folder | White-paper § | Pattern | Foundry/MAF features showcased |
|---|---|---|---|
| `patterns/05-branching-hypotheses` | §8 | 5 hypotheses live at once, evidence prunes | **MAF fan-out/fan-in**, **WorkflowViz** (`make viz`), branch-kill steering, Prompt Shields |
| `patterns/06-memory-augmented` | §9 | Semantic + episodic + procedural memory | **Vector stores + FileSearchTool**, Azure Tables scope/TTL, security-trim ablation, managed-memory path |
| `patterns/07-reflection-skills` | §10 | Reflect → author skill → review gate → improve | Skill library as git artefacts, cross-family review gate, ungoverned ablation |
| `patterns/08-workflow-state-hitl` | §11 + §13 | State machine owns process, agents own judgement | **Durable Functions** (external events, SLA timers, saga compensation), HITL packages, audit trail |

All eleven patterns share one baseline, one adapter file, and one eval runner.
Pattern 03's `VARIANT=maf` is the one place a Microsoft Agent Framework graph
actually executes on the `make run`/`make eval` path rather than only being
diagrammed — see `docs/FOUNDRY-MAF-COVERAGE.md` §5 for exactly what that
does and doesn't extend to.

## The workshop arc

1. **Baseline first.** Every pattern folder ships a `baseline/` single-frontier-call agent
   evaluated on the same dataset. The white paper's own falsifiability test: a pattern earns
   its keep only if it beats this baseline on quality or cost.
2. **Run the pattern**, read the traces in the **Activity tab**, compare runs in the
   **Experiments** view.
3. **Swap variants** (`--variant cheap-model`, `--variant improved-instructions`) and watch
   eval scores and cost-per-run move. This is the "expensive → cheap model" migration story,
   made measurable.
4. **Let the Agent Optimizer rewrite your instructions** and re-evaluate.
5. **Steer a run live** (`make run-interactive` in patterns 02 and 03): veto a
   plan before fan-out spends money, or approve/reject the agent's write with
   a reason it must revise against.

## Quick start

```bash
# 0. Prereqs: az CLI >= 2.67, Python 3.11+ (a venv is recommended), Docker optional (ACR cloud build is used), an Azure sub with Foundry quota.
az login

# 1. Module 0 — deploy SHARED infra once (RG, Foundry account+project, App Insights,
#    AI Search, Storage, Container Apps env for the MCP server). ~5 min.
cd infra/shared && ./deploy.sh

# 2. Pick a pattern. Each is standalone from here.
cd ../../patterns/02-react-tool-loop
make deploy     # provisions pattern-specific infra + deploys hosted agents
make run        # triggers the inbound agent with a sample input
make eval       # runs the Foundry evaluation over data/eval_dataset.jsonl
make eval VARIANT=cheap-model   # same eval, cheaper models — compare in Experiments
make destroy    # tears down pattern resources (shared infra stays)
```

How to run ANY pattern folder (the six make targets, variants, traces, cost, steering, testing): **[HOW-TO-RUN.md](HOW-TO-RUN.md)**.
Full sequencing, prerequisites, roles and quota: **[WORKSHOP.md](WORKSHOP.md)**.
Something broken? **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)**.
Security posture (workshop vs production): **[SECURITY.md](SECURITY.md)**.
Invocation styles, feature coverage, MAF simplifications: **[docs/FOUNDRY-MAF-COVERAGE.md](docs/FOUNDRY-MAF-COVERAGE.md)**.

**Before any delivery, run all three checks (none need Azure or network):**

```bash
python3 scripts/check_package_versions.py   # are the pins current? links changelogs
python3 scripts/verify_offline.py           # does the code still agree with the pins?
python3 scripts/run_ci_smoke.py             # does every pattern's run_case() actually WORK?
```

`verify_offline.py` exercises budgets, cost accounting, every variant file,
every pattern's graders (validated against the openai SDK's `ScoreModelGrader`
model), the deterministic evaluators, and dataset well-formedness — pure
logic, in isolation.

`run_ci_smoke.py` is the other half: it drives all **eleven** patterns'
`run_case()` end-to-end (in ~3 seconds, no live Azure endpoint, no network)
through `common/reasoning_common/fake_backend.py` — a rule-based responder
that reads each prompt's own documented output contract
(`schema_sniffer.py`) and synthesizes a structurally valid fake response, plus
real dispatch to the actual MCP route handlers for tool calls. This is the
gap between "the modules import" and "the patterns work": deterministic
checks, pydantic validation, routing, sandboxing and budget enforcement all
run for real; only the language model itself is faked. `.github/workflows/ci.yml`
runs all three checks on every push.

All pins are exact (`==`) and a clean `pip install` of them was verified to
resolve.

## Repository layout

```
infra/shared/          Module 0: Bicep + az CLI for shared resources; writes .shared-env
common/
  reasoning_common/    Shared Python package: foundry_client adapter, budgets,
                       telemetry, variant loader, eval runner, cost report,
                       sandbox (constrained execution for model-generated code)
  mcp_server/          Real MCP server (FastAPI, streamable-HTTP) deployed to
                       Azure Container Apps; per-pattern route modules
  knowledge/documents/ Fake policy & regulation corpus indexed into AI Search /
                       Foundry IQ knowledge bases
scripts/lib.sh          Shared shell functions (install_shared_reqs,
                        delete_pattern_agents, noop_destroy) sourced by every
                        pattern's deploy.sh/destroy.sh — one implementation
                        instead of eleven copies (see project review item 16).
patterns/common.mk      Shared Makefile targets (deploy/run/eval/eval-smoke/
                        cost/destroy), included by every pattern Makefile.
patterns/<NN-name>/    Standalone pattern project (see its README.md). Its
                       Makefile and infra/*.sh are thin — the shared plumbing
                       above is what they call into.
```

## A note on volatile surfaces

Foundry hosted agents, Agent Optimizer, model router and the Microsoft Agent Framework
(MAF) APIs are evolving quickly. All SDK calls are isolated behind
`common/reasoning_common/foundry_client.py` so fixes are one-file changes. Each README
links the current docs; **verify against them before a customer workshop**.
