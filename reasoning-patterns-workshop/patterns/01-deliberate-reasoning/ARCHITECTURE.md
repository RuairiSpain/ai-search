# Pattern 01 — Deliberate Reasoning (§5)

**Problem class:** correct facts, weak judgement. The first plausible answer is often wrong.
**Scenario:** manufacturing diagnostics — pipeline stalls after an upgrade.

```mermaid
flowchart LR
    Q[Query] --> G[Candidate generator<br/>deployment: small ×N]
    G --> C1[Hypothesis 1]
    G --> C2[Hypothesis 2]
    G --> C3[Hypothesis 3]
    C1 & C2 & C3 --> D{Deterministic check<br/>runbook compliance<br/>NO LLM}
    D -->|pass| J[LLM judge<br/>deployment: nano<br/>evidence-alignment score]
    D -->|fail| X[Rejected + reason logged]
    J --> S[Selector<br/>best candidate + rejected alternatives]
    S --> A[Answer with rationale]
    Q -.-> B[BASELINE: one frontier call] -.-> A2[Answer]
```

Control loop: **generate → evaluate → select** (§3). The deterministic check runs
*before* any judge call — rules the business already trusts are free and unfoolable.
Budgets (`budgets.yaml`) bound candidates, tokens and wall clock.

**State sharing shown here:** in-process workflow state (candidates + scores live in
the MAF workflow context for a single run). Contrast with pattern 03 (typed
contracts between agents) and pattern 02 (thread state in Agent Service).
