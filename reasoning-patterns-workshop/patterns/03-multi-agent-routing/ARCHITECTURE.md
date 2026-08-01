# Pattern 03 — Multi-Agent Orchestration & Model Selection (§12)

**Scenario:** "Where should we invest retention budget next quarter?" — a
decomposable analysis over segment data.

```mermaid
flowchart TD
    Q[Question] --> P[Planner<br/>deployment: frontier — low volume, hard]
    P -->|Plan: typed contract| F{fan-out}
    F --> W1[Worker retrieve<br/>small / router]
    F --> W2[Worker retrieve<br/>small / router]
    F --> W3[Worker analyze<br/>small / router]
    W1 & W2 & W3 -->|WorkerOutput: typed| CK[(Blob checkpoint<br/>fan-out state)]
    CK --> R[Reviewer<br/>deployment: reviewer — DIFFERENT family<br/>cross-family = uncorrelated blind spots]
    R -->|approve| M[Merger<br/>small]
    R -->|revise ≤2×| F
    R -->|reject after cap| H{{Escalate to human}}
    M --> D[Decision + evidence + rejected alternatives]
```

Every handoff is a **pydantic contract** (`common/reasoning_common/contracts.py`)
— §12: "free-text handoffs are where multi-agent systems silently fail." The
reviewer runs a different model family than the generators. Debate is capped at
2 revision rounds, then a human, not a loop.

Expressed as a **MAF workflow** in `src/maf_workflow.py` (executors + fan-out
edges); `src/workflow.py` carries a dependency-free fallback of the same graph
so the pattern runs even when the MAF package version drifts.

**State sharing shown here:** typed contracts on the edges + a **Blob Storage
checkpoint** of fan-out state (survives process death; inspectable mid-run) —
the third mechanism after in-process (01) and thread state (02).
