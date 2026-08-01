# Pattern 04 — Neuro-Symbolic Reasoning (§14)

**Scenario:** bank customer onboarding. The LLM interprets messy requests and
drafts a path; a **deterministic rules engine** (real MCP tool `evaluate_rules`,
versioned code, same input → same output) decides what is *permitted*. Prompts
create tendencies; engines create guarantees.

```mermaid
flowchart LR
    Q[Messy onboarding request] --> E[Case extractor<br/>nano → structured case JSON]
    E --> P[Proposer<br/>small: draft onboarding path]
    E --> R[[Rules engine via MCP<br/>evaluate_rules — KYC/LIM/JUR/DOC<br/>DETERMINISTIC]]
    P --> G{Enforcement layer<br/>code, not model}
    R --> G
    G -->|permitted| X[Explainer<br/>nano: decision citing rule IDs<br/>Directive Art. 12]
    G -->|rejected/held| X
    X --> D[Decision: path + obligations + rules_cited]
```

The enforcement layer is ~20 lines of Python: whatever the proposer says, the
engine's verdict wins. The `prompt-rules-only` variant deletes the engine and
puts the rules in the prompt instead — run the eval and watch the guarantee
become a tendency.

Knowledge base: the (fictional) EU Onboarding Directive is indexed; the
explainer cites Art. 12 (explainability) — decisions without cited rules are
invalid per CP-19 §2.3.
