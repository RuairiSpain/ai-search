---
name: runbook-compliance
description: Mandatory ordering rules from Contoso runbook RB-7 for pipeline-stall
  diagnostics. Always consult this skill for any pipeline stall, batch failure,
  or nightly-job incident before committing to a root cause.
---

# RB-7 compliance

1. Connection-pool metrics are checked FIRST for any post-upgrade pipeline
   stall (58% base rate). A diagnosis that skips this must state why pools are
   already ruled out.
2. Scheduler timezone drift is checked second (22%).
3. Any recommendation must be phrased as *next diagnostic step*, not as a
   confirmed cause, until discriminating evidence exists.

A response that violates rule 1 fails the deterministic compliance check and
never reaches the judge. That is by design.
