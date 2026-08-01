# Contoso Support Runbook RB-7: Pipeline Stall Diagnostics
Version 1.4

Symptom: nightly batch pipeline stalls after upgrade.
Known causes ranked by observed frequency:
1. Connection-pool exhaustion after driver upgrade (58% of cases) — check pool
   metrics before anything else.
2. Scheduler timezone drift (22%) — verify cron TZ matches host TZ.
3. Credential rotation not propagated (12%).
4. Genuine data-volume growth (8%).
A recommendation that commits to a single cause without checking pool metrics
first is non-compliant with this runbook.
