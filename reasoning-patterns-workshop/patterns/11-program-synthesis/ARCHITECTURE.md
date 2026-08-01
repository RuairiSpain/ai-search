# Pattern 11 — Program Synthesis & Test-Repair (§16)

**Scenario:** migrate a legacy config parser to a new typed API. The deliverable
is not code that *looks* right — it is a **passing test run plus the diff**.

```mermaid
flowchart LR
    L[Legacy source + acceptance tests] --> G[Patch generator<br/>deployment: small<br/>writes config.py]
    G --> W[(Isolated workspace<br/>tmp dir, tests READ-ONLY<br/>sha256 checksum per round)]
    W --> T{pytest<br/>subprocess, timeout<br/>DETERMINISTIC verdict}
    T -->|fail| F[Failure analyst<br/>deployment: small<br/>reads tracebacks]
    F -->|repair guidance| G
    T -->|pass| D[Deliverable: green run + unified diff]
    T -->|rounds exhausted| H{{Escalate with failing<br/>tests + best attempt}}
```

The evaluator here is the strongest in the workshop — **executable tests** —
and the checksum guard makes "make the tests pass" mean exactly one thing.
The weak-tests variant runs 3 of 8 tests: "is the test suite strong enough
that passing it means something?" answered by ablation. Production shape:
same loop inside CI (GitHub Actions / Azure DevOps) with scanning gates,
GitHub Copilot coding agent or codex-class models doing generation.
