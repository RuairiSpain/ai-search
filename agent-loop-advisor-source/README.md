# Agent Loop Advisor

**Describe a business problem in plain English; get an honest, verified
recommendation for the agentic reasoning architecture that fits it — or an
honest "you don't need one."**

The Agent Loop Advisor turns a scenario into a recommended reasoning-pattern
architecture, backed by the **Pattern Compiler** — a deterministic engine that
diagnoses the problem, composes candidate architectures from a catalogue of 14
patterns, kills the illegal ones for free, and presents three ranked options
plus a baseline. It runs as a **conversational agent** (for Copilot Studio /
Microsoft 365) over an **MCP server backend**, and it can emit a ready-to-build
**Azure AI Foundry project**.

- Pattern reference with diagrams: [`agent_pattern.md`](./agent_pattern.md)
- The deterministic core has **no LLM and no network dependency** — it is fully
  testable and reproducible.

---

## What it does

| Outcome | Meaning |
|---|---|
| **Three cards** | Minimal / Balanced / Ambitious architectures on a fixed machinery ladder, plus the baseline. One card is flagged **recommended** (the best fit). Each shows the composition, computed cost & latency, where humans sit, and the top risk. |
| **Baseline recommended** | A single grounded agent is enough — this is a *retrieval* problem, not a *reasoning* one. It will not upsell you machinery. |
| **Primitive scaffold** | No catalogue pattern fits cleanly, so it composes one from reasoning primitives, **marked UNVERIFIED**, with the loops, evaluators and dependencies you must complete. |
| **Baseline fallback** | The document did not contain enough to design a system. It falls back to the grounded baseline as a **safe default, not a diagnosis**, and lists the questions that would unlock a real recommendation. |

Two honesty guarantees are enforced in code, not just prose: **cost figures are
computed from budget data, never invented** (unverified scaffolds show none), and
**every recommendation carries a named evaluator** — no evaluator, no verified
build.

---

## Architecture

```
architect  <->  Copilot Studio agent (LLM: conversation)  <->  MCP  <->  Pattern Compiler (deterministic core)
```

The agent's model is good at *conversation* — asking the architect the right
questions. The MCP backend is good at *deterministic logic* — diagnosis,
legality, honest cost. Neither does the other's job.

**MCP tools exposed:**

| Tool | Purpose |
|---|---|
| `diagnose_requirements` | Diagnose the scenario; return the clarifying questions to ask. |
| `recommend_patterns` | Return the recommendation (three cards / baseline / scaffold / fallback). |
| `explain_pattern` | Explain one catalogue pattern. |
| `list_catalogue` | List every catalogue pattern, its role, and when it beats a baseline. |
| `get_pattern_diagram` | Mermaid diagram of a pattern or a composition. |
| `validate_composition` | Legality-check a composition the architect proposes. |
| `emit_foundry_project` | Emit a Foundry/MAF project scaffold. |

---

## Run it locally

```bash
pip install -e .

# The recommender as a CLI
patcomp compile requirements.md            # interview + recommendation
patcomp compile requirements.md --no-interview --diagram
patcomp ask                                # interview only, no document
patcomp explain 05                         # what a pattern is and when it wins
patcomp diagram --pattern 05               # a pattern's Mermaid diagram
patcomp diagram --composition "guard(sequence(10,05),04)"
patcomp emit requirements.md --out ./solution     # emit a Foundry project
patcomp emit --all --zip reasoning-patterns.zip   # the whole catalogue

# The MCP server
patcomp-mcp --http --port 8080             # Streamable HTTP (Copilot Studio / Foundry)
patcomp-mcp --stdio                        # stdio (local MCP clients)

# Tests (stdlib unittest — no extra deps)
python -m unittest discover -s tests
```

Health check: `GET /health`. MCP endpoint: `POST /mcp` (JSON-RPC 2.0).

---

## How to use it — the questions

You do not fill in a form. You describe the problem in a few sentences, and the
Advisor asks a **short, adaptive set of questions** (three to five) — only the
ones that change the recommendation. Answer them conversationally.

**1. Frame the problem.** The Advisor first wants to know where your current
system falls short:

> *"Where does it fail — finding the right information, or deciding what to do
> with it?"*

This separates **retrieval** work (which grounding solves) from **reasoning**
work (which the patterns solve). If it's retrieval, you'll be told you don't need
orchestration.

**2. Confirm the diagnosis.** It proposes the problem family it detected and asks
you to confirm:

> *"Does this describe your problem: 'the first plausible explanation is often
> wrong'?"* — Yes / No

**3. How would you know an answer was good?** *(the one question that always
matters)*

> A **test** can check it - a **rule** can check it - another **model** can
> judge it - only a **human** can judge it - **we can't say yet**

This is the hard gate. "We can't say yet" is a valid, common answer — it means
the first project is defining the evaluator, and the Advisor will say so rather
than pretend.

**4. What has to be true before it acts?**

> A **human approves** first - a **rule must decide**, every time - **both** -
> **neither** — it can act on its own

This draws the control boundary — which decisions are deterministic and which
need approval.

**5. Does it write to a real system, or only read and recommend?**

Only asked if a write action was detected. A write boundary must be guarded by a
rules engine or a human gate, or the build fails.

From your answers it produces the recommendation. If you answer everything, you
get a confident three-card result; if the scenario is thin, it degrades honestly
to a scaffold or a low-confidence baseline with the questions that would unlock
more.

**Example**

> *"Our assistant retrieves the right policy clauses but its accept/reject
> recommendations are inconsistent."*

-> diagnosed as a **judgement** problem (not retrieval) -> after two questions
(evaluator = human, approval = yes) -> recommends **deliberate reasoning guarded
by a rules engine**, ~EUR0.24/task, with the baseline shown as the honest floor
and a diagram of the flow.

---

## Deploy to Copilot Studio

The Advisor connects to Copilot Studio as an MCP server via a custom connector.

1. **Deploy the MCP server** somewhere with HTTPS (see *Deploy to Foundry /
   Azure* below). Note the ingress URL, e.g. `https://<your-host>/mcp`.
2. **Edit the connector spec** `agent/mcp-connector.swagger.yaml` — set `host:`
   to your deployed hostname. It already carries
   `x-ms-agentic-protocol: mcp-streamable-1.0`, which is how Copilot Studio
   recognises an MCP server.
3. In **Copilot Studio -> Tools -> Add a tool -> New tool -> Custom connector ->
   Import an OpenAPI file**, select the edited spec. The seven tools above are
   discovered automatically.
4. **Add the connector to your agent**, and paste `agent/instructions.md` into
   the agent's instructions.
5. **Test** in the Copilot Studio test pane:
   *"My retrieval works but the recommendations are inconsistent — what pattern
   do I need?"*

To publish to Microsoft 365, the `agent/` folder is a declarative-agent app
package (`declarative-agent.json` + `m365-manifest.json` + the connector). Fill
the placeholders (a GUID, your host in `validDomains`, and two icons), zip it,
and upload it in the Microsoft 365 Admin Center. It then appears in the M365
Copilot agents list for assigned users.

---

## Deploy to Foundry / Azure

The server is a single stdlib-only Python process (plus PyYAML). It scales to
zero and starts fast — no model call, no outbound network, no database.

**Container**

```bash
docker build -t patcomp-mcp:0.1 .
docker run -p 8080:8080 patcomp-mcp:0.1
```

**Azure Container Apps** (recommended for Copilot Studio — HTTPS, scale-to-zero,
stable URL)

```bash
az containerapp env create -g <rg> -n patcomp-env -l <region>
az containerapp create -g <rg> -n patcomp-mcp \
  --environment patcomp-env \
  --image <registry>/patcomp-mcp:0.1 \
  --target-port 8080 --ingress external \
  --min-replicas 0 --max-replicas 3 --cpu 0.25 --memory 0.5Gi
# the printed FQDN + /mcp is your connector host
```

**Azure AI Foundry** — two ways:

1. **As a custom MCP tool for a Foundry Agent.** Deploy the container (Container
   Apps or a Foundry managed endpoint) and register the `/mcp` URL as an MCP tool
   on your Foundry agent. The Foundry agent's model runs the conversation; this
   server runs the logic — the same split Copilot Studio uses.
2. **As a managed online endpoint.** Push the image to your Foundry/AML registry
   and create an online endpoint exposing port 8080.

Full step-by-step (Container Apps, Foundry, Copilot Studio, M365 publish) is in
`deploy/DEPLOY.md`. Authentication is expected to be handled at the platform
ingress (Container Apps auth, Foundry keys, or API Management) — do not expose
`/mcp` openly.

---

## The emitted Foundry project

`emit_foundry_project` (or `patcomp emit`) produces a customisable **Azure AI
Foundry project** using Microsoft libraries — **Microsoft Agent Framework (MAF)**
for agents and workflows, **Azure AI Agent Service** (`azure-ai-projects`) for
hosting, **azure-ai-evaluation** for evaluators, and per-pattern Azure services.
Each pattern folder carries agents, skills, an evaluator, a harness, and its
diagram; the root carries shared clients, an `infra/main.bicep` skeleton, an
`azure.yaml` for `azd`, and a free structural verifier.

> It is a **structural scaffold**, not a running system: tools default to
> read-only, every pattern ships an evaluator, `.env.sample` holds no secrets,
> and MAF / Agent Service APIs are **preview** — pin your versions and fill the
> `TODO`s. Verify with `python verify_structure.py`.

---

## Design principles

- **Deterministic core.** Diagnosis, legality and cost need no model — an LLM
  diagnoser is an optional enhancement, never a dependency.
- **Honesty over polish.** The most valuable output is often "you don't need
  this." Unverified work is marked; a fallback is never dressed up as a
  diagnosis.
- **The requirements text is data, never instruction.** Intake flags injection
  markers and never lets scenario text become a directive.
- **Every claim is traceable.** Cost from budget data, patterns from the
  catalogue, diagrams from the same engine that recommends them.

See [`agent_pattern.md`](./agent_pattern.md) for the full pattern catalogue —
each design pattern's diagram, description, and how it differs from the other
choices.
