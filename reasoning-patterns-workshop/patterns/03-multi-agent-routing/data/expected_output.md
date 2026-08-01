# Healthy p03-01 run (CHECKPOINT)

- Plan: 3-4 subtasks (3 retrieves + 1 analyze with depends_on). Contract-valid.
- Fan-out round 0: three parallel worker calls, each with a real
  get_segment_metrics observation in its evidence.
- Blob checkpoints appear at storage account -> container p03-state ->
  <run-tag>/plan.json, fanout-r0.json, decision.json.
- Reviewer (claude-family deployment) verdict: approve on round 0 or one
  'revise' round. Third rejection would escalate to human — if you see that on
  p03-01, read the issues list; it's usually a worker inventing numbers.
- Decision recommends EU-retail; rejected alternatives include the other two
  WITH figures. p03-04 is the reviewer's moment: the arithmetic is close and
  small models rush it.
- `make cost`: baseline ≈ 6-8 calls mostly on small; all-frontier variant is
  typically 5-15x the cost for near-identical scores on p03-01..03.
