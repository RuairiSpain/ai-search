# What a healthy p01-01 run looks like (CHECKPOINT reference)

- 3 candidates generated on `small`; typically 0–1 rejected by the deterministic
  RB-7 check, the rest judged on `nano`.
- Final response contains: a *next diagnostic step* naming connection-pool
  metrics; the leading hypothesis; evidence; at least one alternative; any
  rejected candidates with reasons.
- Trace: `accepted + rejected == 3`, `budget.llm_calls <= 7`, elapsed < 60s.
- Cost (illustrative prices): ≈ $0.005–0.02 per run on baseline;
  single-frontier variant ≈ 2–6× that. Run `make cost` after both.
