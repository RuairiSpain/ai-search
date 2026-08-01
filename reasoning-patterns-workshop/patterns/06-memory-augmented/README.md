# Pattern 06 — Memory-Augmented Reasoning (white paper §9)

Four memory types made physical: **working** (thread), **semantic**
(Foundry vector store + FileSearchTool), **episodic** (Azure Tables with
scope + TTL + poisoned flags), **procedural** (git-versioned runbooks as
skills). The read/write policy layer is what makes it enterprise-grade.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy   # grants Table role, registers one agent per seed user
```

Uses **verified** azure-ai-agents 1.1.0 methods: `files.upload_and_poll`,
`vector_stores.create_and_poll`, `FileSearchTool(vector_store_ids=[...])`.

## 2. Run

The dataset carries JSON per row so `run_case` can carry the multi-session
scenario (user_id, session_id, caller_user_id, message, optional
record_episode). `make run` executes the sample session-3 query.

```bash
make run
```

## 3. The experiment

```bash
make eval                             # baseline (explicit vector store + Tables)
make eval VARIANT=no-memory           # ablation: memory disabled
make eval VARIANT=no-security-trim    # ablation: read-time scope check disabled
make eval VARIANT=managed             # Foundry built-in memory (preview-gated)
```

Portal → Evaluations:

- **baseline vs no-memory** on rows p06-01→03: watch continuity collapse.
  Row 03 in the no-memory column typically re-asks for the environment or
  re-suggests pool checks — memory earns its complexity.
- **no-security-trim on p06-05**: caller u-bob asks for u-alice's summary;
  the ablated variant leaks it. This is why §9 says recall must respect
  caller permissions at READ time.
- **p06-06** tests poisoned-memory handling; the agent should recall the
  entry as `[UNVERIFIED customer report]` and NOT adopt "skip pool checks"
  as policy.
- **managed**: if your tenant lacks Agent Service built-in memory
  (preview), the code falls back to explicit and marks `managed_fallback=true`
  in the trace so the Experiments row is still interpretable.

## 3b. Distinguishing semantic from episodic recall

The trace now separates three states that used to be indistinguishable from
the outside: `semantic_tool_attached` (was FileSearchTool even wired onto the
agent — false if the vector store failed to provision), and
`semantic_recall_invoked` (did the model actually CALL file_search this run,
independent of `episodes_recalled`, the episodic count). A "the copilot
remembered things" pass that shows `semantic_recall_invoked: false` and a
nonzero `episodes_recalled` means the win came from episodic memory alone —
worth knowing before crediting the vector store for something it didn't do.

## 4. Traces

App Insights → service `pattern-06-memory`. Spans: `p06.recall_episodic`
(attributes `user`, `caller` — the trim is visible), then `p06.run`. Row
p06-05's baseline trace should show `episodes_recalled=0` because scope
enforced; the ablation shows a non-zero recall.

## 5. Tear down

```bash
make destroy   # removes p06-* agents, vector stores, and the Table
```

## MAF/Foundry features exercised

- Verified Agents 1.1.0 vector-store lifecycle: upload → create_and_poll →
  `FileSearchTool` attached via `tool_resources` on the agent.
- Real Azure Tables via `azure-data-tables==12.7.0` for episodic memory with
  server-side partition scoping.
- Optional Foundry built-in memory path with graceful fallback (variant
  logging preserved so managed-vs-explicit comparisons work in Experiments).
