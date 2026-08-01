---
name: hypothesis-generation
description: How to generate strong, distinct diagnostic hypotheses for equipment
  and pipeline incidents. Use this skill whenever asked to propose a root cause,
  diagnose a fault, or explain why a system is failing — even if the request
  doesn't use the word "hypothesis".
---

# Generating diagnostic hypotheses

A good hypothesis is falsifiable, distinct, and evidence-ranked.

- **Falsifiable**: name the single observation that would eliminate it. If no
  observation could eliminate it, it is a narrative, not a hypothesis.
- **Distinct**: differ from already-proposed hypotheses in *mechanism*, not
  wording. "Pool exhaustion" and "too many connections" are the same hypothesis.
- **Evidence-ranked**: prefer mechanisms with base-rate support (runbook
  frequencies) over exotic ones, but never suppress a low-frequency mechanism
  that uniquely explains an observation the frequent ones cannot.
- When evidence is insufficient to discriminate, the correct diagnostic step is
  a data request, not a guess.
