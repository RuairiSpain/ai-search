# Invocation Styles, Feature Coverage, and MAF Simplifications

## 1. Invoke-style vs messages-style: what each pattern uses and why

Two invocation surfaces exist in this repo, used deliberately:

| Pattern | Surface | Style | Why this one |
|---|---|---|---|
| 01 deliberate | `chat.completions` via project client | **messages-style** (raw `[{role, content}]` lists per call) | The control loop *is* the pattern; each generator/judge call is stateless and the orchestration owns all state. Thread machinery would add nothing. |
| 02 ReAct | Foundry Agents (`threads → messages → runs`) | **agent-invoke style** (create thread, post message, `create_and_process` the run) | The platform owns the ReAct loop; thread state carries observations between steps. This is the only pattern that *needs* server-side conversation state. |
| 03 multi-agent | `chat.completions` per role (+ MAF executors in `maf_workflow.py`) | **messages-style** between typed-contract boundaries | Handoffs are pydantic objects, not conversations. A thread per worker would be state you'd then have to govern for no benefit. |
| 04 neuro-symbolic | `chat.completions` + deterministic MCP call | **messages-style** | Three stateless model calls around a rules engine; the engine verdict, not a conversation, is the state. |

**Do any folders need message-style changed?** No — but one distinction matters
for phase 2: the **memory pattern (§9)** and **workflow-state pattern (§11)**
should use the agent-invoke/thread surface (or MAF `ChatAgent` with a thread),
because persistent server-side state is the point of those patterns. The rule
of thumb this repo teaches: *use threads when conversation state is the
feature; use messages-style when your orchestration owns the state.* Mixing
them arbitrarily is how you end up with two sources of truth.

MAF note: `ChatAgent.run("...")` is itself messages-style underneath — MAF
normalises a string, a message, or a list of messages into the same request.
The choice above is about *where state lives*, not syntax.

## 2. Foundry features: shown vs not yet shown

**Shown in phase 1:** model deployments by role · declarative (prompt) agents ·
hosted-agent container path · MCP tools with allowlists + approval mode ·
AI Search knowledge attachment and its ablation · cloud evaluations, custom
rubric graders, Experiments comparison · Agent Optimizer (portal + transparent
fallback loop) · tracing/Activity tab + App Insights · budgets & cost
accounting · model router deployment · cross-family review (Claude reviewing
GPT output).

**Not yet shown — candidates for phase 2, roughly in teaching-value order:**

| Feature | Natural home |
|---|---|
| Agent Service **built-in memory** (preview) | ✅ pattern 06 (`VARIANT=managed`, falls back to explicit) |")
| **Continuous evaluation** on live traffic → App Insights dashboards | Extend §17 flywheel across all patterns |
| **AI Red Teaming agent / adversarial simulators** | Safety module; seeds more failure rows |
| **Content safety / Prompt Shields** on agent I/O | Pattern 02 (defence-in-depth for the injection row — today only instructions + evals defend it) |
| **Connected agents / A2A** (cross-stack agent calls) | Multi-agent phase-2 extension |
| File search built-in tool | ✅ pattern 06 (vector stores); code interpreter still open |
| **Logic Apps tools + Teams approvals** | Human-in-the-loop pattern (§13) |
| **Durable Functions** orchestration | ✅ pattern 08 (`functions_app/`, external events + SLA timers + saga) |
| **RFT / distillation / stored completions** | §19 module once eval datasets are mature |
| **APIM AI Gateway** (token quotas, semantic caching) | §18 cost module — worth a shared-infra option |
| **Fabric IQ Ontology + Graph** | Graph pattern (§15) |
| **Foundry Benchmarks** (preview) | Release-gate exercise extension |

## 3. MAF SDK: methods that would simplify current logic

Concrete simplification opportunities, each marked adopt-now vs phase-2. The
code keeps its dependency-free fallbacks on purpose (the workshop must survive
SDK drift), so "adopt" means: in `maf_workflow.py` first.

1. **Structured outputs instead of `chat_json` + `model_validate`.**
   MAF/OpenAI-surface `response_format=<pydantic model>` gives you the parsed,
   validated object in one step. Would delete the retry-on-bad-JSON logic in
   `foundry_client.chat_json` and the manual `Plan.model_validate(...)` calls
   in pattern 03. *Adopt in maf_workflow.py now; keep chat_json as fallback.*

2. **`MCPStreamableHTTPTool`** — MAF's first-class MCP client tool. Replaces
   our hand-rolled `reasoning_common/mcp_client.py` for code-side executors
   (patterns 03/04): the tool handles session lifecycle, schema discovery and
   retries, and the model can *choose* tool arguments rather than code
   hard-matching segment names (see the brittle segment-string matching in
   pattern 03's `work()` — a known weakness the tool would remove).

3. **Fan-out/fan-in edges** (`add_fan_out_edges` / `add_fan_in_edges` on
   `WorkflowBuilder`). Pattern 03's `ThreadPoolExecutor` block is exactly this,
   hand-built. ✅ ADOPTED in pattern 05 (`src/maf_workflow.py`), alongside
   `WorkflowViz.to_mermaid()` via `make viz` — the diagram is generated from
   the built graph, so it cannot drift from the code.

4. **Built-in checkpointing** (`with_checkpointing(CheckpointStorage)`).
   Replaces our custom `_checkpoint()` blob writer with resumable workflow
   state — and phase 2's workflow-state pattern should use it rather than
   growing our own. Our blob writer stays as the "here's what it does under
   the hood" exhibit.

5. **`ChatAgent` as executor + agents-as-tools.** Planner/reviewer/merger
   could each be a `ChatAgent` with instructions loaded from our markdown, and
   workers could be exposed to the planner *as tools* — collapsing the
   plan→dispatch code. Deliberately not adopted in phase 1: the explicit loop
   is the teaching artefact. Phase 2's reflection pattern is the right place.

6. **Handoff / group-chat orchestration patterns** (AutoGen lineage, now in
   MAF). Relevant to §12's conversational variants; our sequential
   review-revise loop is simpler and bounded, which is the point being taught,
   but a handoff demo belongs in phase 2.

7. **Middleware** (request/response interceptors). Our budget charging and
   cost ledger calls are scattered through node functions; MAF middleware
   would centralise both (charge/record on every model call automatically).
   Worth adopting once, in `maf_workflow.py`, as the "production-shape"
   contrast to the explicit version.

## 4. Human steering at contract boundaries

Both steering mechanisms are implemented (phase 1) and follow one rule: steer
at **typed boundaries** (a Plan, a review verdict, a gated tool call), never
mid-generation — that keeps interventions loggable, evaluable and resumable.

- **Pattern 03** `make run-interactive`: pause after the Plan (veto/edit
  subtasks before fan-out spends money) and on a reviewer `revise` verdict
  (your guidance replaces the reviewer's). Fallback impl = CLI hook;
  MAF-native impl = `ctx.request_info` → `wf.run(responses=...)`
  (`build_steerable` in maf_workflow.py).
- **Pattern 02** `make run-interactive`: approval-gated `draft_offer` — the
  distinction taught is *approval gates a commit; steering changes a
  trajectory*, and a platform-owned ReAct loop only offers the former.
- Budgets pause the wall clock during human waits (`Budget.human_wait`);
  interventions land as structured data in traces and `runs/steer-*.jsonl`
  (§13: critiques are learning signals, bare vetoes are noise).
- Phase 2 scales the same mechanism: streaming branch-kill in pattern 05,
  Durable external events with SLA timers in pattern 08.

## 5. Which MAF graphs actually EXECUTE vs. only diagram

Two things called "maf_workflow.py" exist in this repo, and they are not the
same claim. Pattern 03's `variants/maf.yaml` (`engine: maf`) genuinely routes
`make run`/`make eval` through `WorkflowBuilder`'s real graph — planner,
fan-out workers, reviewer, merger all execute as MAF executors, end to end,
producing the same Decision output as the dependency-free path. That's the
one place in the workshop where "we use MAF" means the graph actually ran,
not just that a diagram was generated from one.

Pattern 05's `maf_workflow.py` is scoped narrower on purpose: it demonstrates
`add_fan_out_edges`/`add_fan_in_edges` for ONE round of branch expansion, and
backs `make viz` (WorkflowViz on the real built graph) and the
`build_steerable`-style request_info steering path in pattern 03. It is not
wired into pattern 05's `make run`/`make eval` — the multi-round prune loop
that pattern needs stays in workflow.py, since driving that shape through
MAF would mean rebuilding the graph fresh every round from outside it, a
real extension not attempted here. Don't extrapolate pattern 03's coverage
to pattern 05: say "MAF executes end-to-end in pattern 03, and demonstrates
the fan-out primitive in pattern 05" — that's the accurate claim.

The one-line summary for attendees: everything MAF automates here, phase 1
first shows you by hand — that ordering is pedagogy, not ignorance of the SDK.
