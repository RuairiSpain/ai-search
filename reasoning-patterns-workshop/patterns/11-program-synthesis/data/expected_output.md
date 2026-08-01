# Healthy p11-01 run (CHECKPOINT)

- Round 0 usually fails 2-4 tests (the KeyError/default combination and
  malformed-line ValueError are the common misses); analyst names root causes;
  round 1 or 2 goes green. trace.rounds shows the arc.
- Deliverables land in runs/: <tag>-config.py and <tag>.diff.
- Response states validation status + the two guarantees (output only lands in
  config.py; tests checksummed) — the §14-inside-§16 talking point.
- no-repair variant: expect ~40-70% first-shot pass on `small` — the repair
  loop's value in one number.
- weak-tests variant: green quickly. Then `make verify-full` runs the FULL
  suite over its output — the failures that appear are exactly what a weak
  suite ships. That table is the §16 CSA prompt ("is the test suite strong
  enough that passing it means something?") answered empirically.
