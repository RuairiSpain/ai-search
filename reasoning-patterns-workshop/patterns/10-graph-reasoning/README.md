# Pattern 10 — Graph Reasoning (white paper §15)

Every transaction document in this scenario looks clean. The fraud ring only
exists in the *relationships* — shared devices and payment instruments across
accounts — and the agent's deliverable is a verdict where **the relationship
map is both the evidence and the explanation**.

Diagram + ontology: [ARCHITECTURE.md](ARCHITECTURE.md), [ontology.yaml](ontology.yaml)
· Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy   # verifies the seeded graph on the shared MCP server
```

If the check fails, re-run `infra/shared/deploy.sh` once — phase 3 added the
graph routes to the MCP image.

## 2. Run

```bash
make run
```

Watch the traversal: the model plans one hop at a time (`get_entity` →
`get_neighbors` → `find_paths`), code executes it, and the log accumulates.
The final verdict cites explicit chains: `A2 -uses_device-> D2 <-uses_device- A4`.

## 3. The experiment

```bash
make eval                    # traversal
make eval VARIANT=docs-only  # same questions, per-account documents, no tools
make eval VARIANT=frontier
```

Compare in Experiments:

- **docs-only** is the §15 thesis as an ablation: a relationship problem can't
  be solved by reading documents harder. The honest docs-only answer is "cannot
  determine" — watch whether it stays honest or invents links.
- **p10-03** is the entity-resolution trap (two "John Smith" accounts): the
  classic small-model failure is merging by name; the `entity-resolution`
  skill is what's under test. Delete the skill's first rule and re-eval to
  prove it's carrying weight.
- **p10-02/04** test benign-link hygiene and "no path" as a reportable
  finding; **p10-05** is the injection row (planted instruction in D2's notes,
  Prompt Shields verdict in the trace).

## 4. Traces

App Insights → service `pattern-10-graph`: one `p10.traverse` span with the
hop list, then `p10.synthesise` (attribute `hops`). The hop count per row is
the efficiency metric — compare baseline vs frontier: does the bigger model
actually take fewer hops, or just cost more per hop?

## 5. Fabric IQ (production target)

This folder's `ontology.yaml` + MCP tools are the workshop-scale stand-in for
**Fabric IQ Ontology + Graph**: define the same entities/relations in the
Ontology (preview), load the operational data into Graph (GA, GQL / ISO 39075),
and swap `get_neighbors`/`find_paths` for NL-to-GQL — the planner/synthesist
agents and all evals carry over unchanged. That separation (agent logic vs
graph substrate) is the architecture point worth making to customers.
