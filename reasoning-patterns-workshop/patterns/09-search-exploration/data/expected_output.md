# Healthy p09-01 run (CHECKPOINT)

- 8 candidates generated on `small`; typically 2-4 die on the FREE constraint
  check (dependency order is the usual killer) — see trace.rejected reasons.
- Survivors scored on `nano`; top-3 deep-analysed on `small`.
- Response: recommended waves + strategy + risks with mitigations + the other
  two deep-analysed alternatives WITH their risk scores + count of
  constraint-killed candidates + (usually) a Prompt Shields note about S8.
- `make cost`: baseline vs no-precheck on the SAME rows is the exhibit —
  no-precheck typically runs ~1.5-2x the model calls for the same answer,
  and its scorer occasionally rates an invalid sequence highly (search
  amplifying a bad evaluator: §7's warning, on the projector).
- p09-02/03 are the infeasibility rows: the correct output is a refusal to
  plan, with the arithmetic. A variant that "solves" them is failing.
