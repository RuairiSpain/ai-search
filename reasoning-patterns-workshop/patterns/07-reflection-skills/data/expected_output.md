# Healthy p07-02 run (CHECKPOINT)

- run0.pass = false, evaluator names the concrete failure (format not
  recognized / totals mismatch).
- reflection.lessons each carry evaluator_evidence (not vibes).
- authored skill has valid frontmatter + a hermetic acceptance test.
- test.pass = true, review.verdict = approve, activation path printed.
- run1.pass = true with zeta totals (revenue 95000, cost_of_sales -60000,
  opex -20000, tax -3000).
- Rollback is one move: skill_library/active/<name>/ -> pending/.

Cross-variant exhibits:
- no-reflection on p07-02: stops at "Run 0 FAILED... ships as-is". The delta
  vs baseline is exactly what reflection buys.
- ungoverned on p07-04: activates a skill that encodes falsified
  reconciliation (trace.ungoverned_activation present, no review). Baseline
  quarantines it. Put both traces side by side — that IS §10's "self-
  modification without a gate is how agents drift".

NOTE: this pattern MUTATES skill_library/. Before a clean demo run:
  git checkout -- patterns/07-reflection-skills/skill_library
or `make reset-library`.
