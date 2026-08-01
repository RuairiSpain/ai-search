---
name: minimal-diffs
description: Discipline for repair rounds in generate-test-repair loops. Apply
  to ANY code-fixing, patching, or migration task with an existing test signal,
  even when "diff" isn't mentioned.
---

# Minimal-diff repair

- Fix the failing tests, only the failing tests. A repair that rewrites
  passing code is a regression risk wearing a fix's clothes.
- Root-cause in the code, not the spec: if a test seems wrong, flag it for a
  human — the loop never touches tests.
- One conceptual change per round. Interleaved fixes make the next failure
  unattributable.
- Preserve names and signatures the tests import; churn there fails everything
  at collection time.
