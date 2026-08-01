# Pattern 09 — Search-Based Reasoning & Controlled Exploration (§7)

**Scenario:** the white paper's retailer modernisation — "what should we
migrate first?" is planning, not retrieval. The answer is one point in a space
of valid wave sequences.

```mermaid
flowchart LR
    Q[Goal + constraints] --> C[Catalog via MCP<br/>get_system_catalog]
    C --> G[Generate N candidate sequences<br/>deployment: small, cheap breadth]
    G --> K{Deterministic constraint check<br/>dependencies, downtime, wave size<br/>NO LLM — invalid dies FREE}
    K -->|invalid| X[Rejected + violation logged]
    K -->|valid| S[Score survivors<br/>deployment: nano]
    S --> T[Deepen TOP-K only<br/>risk analysis, deployment: small]
    T --> R[Recommendation + rejected<br/>alternatives + risk notes]
    Q -.-> B[BASELINE: one frontier call] -.-> R
```

The §7 resource-allocation idea, live: **spend breadth cheaply, kill invalid
options for free, spend depth only where a better decision is likely.** The
`no-precheck` ablation scores every candidate with a model instead — run
`make cost` after both and the argument makes itself.

Observation hygiene: catalog notes are third-party text (S8 carries a planted
instruction); observations pass through **Prompt Shields**
(`reasoning_common.safety`) before reaching any model, logged either way.
