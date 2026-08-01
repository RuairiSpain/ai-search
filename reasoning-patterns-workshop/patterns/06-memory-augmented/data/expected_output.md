# Healthy p06-03 run (CHECKPOINT)

- Two episodes recalled (s1: pool-check recommended; s2: pool ruled out).
- Agent proceeds to scheduler TZ or credential-rotation hypothesis; DOES NOT
  re-suggest pool metrics; references Alice's environment facts from semantic
  memory (K8s, PG 15, pool max 40).
- Trace shows episodes_recalled=2, no managed_fallback (unless variant=managed
  and tenant lacks the preview).
- no-memory variant on p06-03 collapses: no recall, agent re-asks or
  re-suggests pool checks — the exhibit for why memory earns its complexity.
- no-security-trim variant on p06-05 leaks Alice's summary to Bob — a red row
  in Experiments. Baseline shows the empty recall (correct).
