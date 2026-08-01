# Healthy p05-01 run (CHECKPOINT)

- Round 0: 5 branches generated. Expansion typically pulls auth events
  (multiple branches), travel records (H1), oauth grants (H3), mailbox rules
  (H3 after seeing app grant), incidents (H3 pattern-matches INC-4411).
- Scoring on `nano`: H1 (travel) and H3 (OAuth compromise) rise; H4
  (service-account) and H5 (config false-positive) fall.
- Prune round 1: usually kills 2 branches (score < 3.0). Round 2 sharpens.
- Verdict: BOTH H1 and H3 true — travel explains the geo, MailSyncPro explains
  the actual compromise (forwarding rule + unverified publisher + INC-4411).
  Eliminated branches cite the specific observation that killed each.
- Prompt Shields note appears in the response (D2's publisher description
  flagged; treated as data).
- no-pruning variant: 3 rounds x 5 branches = 15 expansions + 15 scores vs
  baseline's ~8 + ~8. `make cost` shows what pruning bought you.
- narrow (top-2) variant on p05-01 is the risk demo: early beam can drop H3
  before oauth evidence lands. Delayed commitment as a variant.
