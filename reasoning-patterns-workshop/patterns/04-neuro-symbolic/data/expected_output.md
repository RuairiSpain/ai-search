# Healthy p04-01 run (CHECKPOINT)

- Trace: extracted case {risk_score: 74, pep: true, exposure_eur: 250000,
  id_verified: true}; engine verdict permitted=true with KYC-001, KYC-014,
  LIM-203 triggered; outcome proceed_with_obligations.
- Response: outcome first, then each rule ID with one-clause explanation and an
  owner per obligation (compliance officer, VP), Art. 12 referenced.
- Spans: p04.extract -> p04.propose -> p04.rules_engine -> p04.explain.
- The interesting comparison is p04-04 across variants: baseline rejects 100%
  of the time (the engine guarantees it); prompt-rules-only usually rejects —
  usually is the point.
