# Golden-set expansion methodology

Why this document exists: the first two rounds of diagnosis-recall work
(2026-08-01) improved the golden set's own numbers by looking directly at
which case failed and hand-crafting an evidence term to fix it, then
re-running the same set and reporting the new number. That is test-set
leakage — the numbers were never validated against anything the tuning
process hadn't already seen. This document is the fix: a phased process with
a genuine held-out cohort, a precision metric (not just recall), and a
registry-based check for cross-signature collisions, so future rounds
produce numbers that are actually trustworthy rather than just improving.

## Phases

**Phase 0 — measurement tooling (done, 2026-08-01).**
- `Row.diagnosis_extra` / `Row.diagnosis_precision` in `goldenset.py`:
  precision counterpart to the existing `diagnosis_recall`. Recall alone
  can't see a widened evidence term that fixes its target case while
  quietly starting to fire on others — precision can.
- `metrics()` / `report()` gained a `cohort` filter and now report
  `diagnosis_precision`, `cases_with_extra_diagnosis`, and a breakdown of
  which signatures over-fire (`over_fired_signatures`). `patcomp goldenset`
  without `--cohort` now prints a warning that the number mixes cohorts and
  isn't one to quote.
- `Row.cohort` (`"tuning"` | `"validation"`), defaulting to `"tuning"` for
  cases with no explicit field. All 26 cases as of this writing are
  `tuning` — every one has already informed an evidence-list edit, so none
  of them are a valid holdout.
- `src/patcomp/signature_audit.py`: a registry of signature pairs known to
  share vocabulary territory (`CONFUSABLE_PAIRS`), checked by running each
  side's evidence terms against a probe corpus built from the *other*
  side's `problem` text and pattern `summary`/`beats_baseline_when` — real
  catalogue content, never invented. `patcomp audit-signatures` prints
  findings; it is informational, not a CI gate (diagnosis is deliberately
  multi-label, so some overlap is fine). The actual gate lives in
  `tests/test_patcomp.py::TestSignatureAudit`, which pins the currently
  *accepted* collisions and fails if a new, unreviewed one appears.
- Found immediately on first run: `weak_judgement`'s "first answer" also
  reads as `multiple_interpretations` (the already-known 01-vs-05 boundary
  fuzziness), and `workflow_too_large`'s "breadth" also reads as
  `planning_under_constraints` (pattern 09's own summary literally says
  "Breadth on a cheap model..."). Both are pinned as accepted, unfixed —
  fixing evidence lists is Phase 2 work, not Phase 0.
- The precision metric also caught something the collision registry
  wouldn't: `vendor-onboarding-case-management`'s "second approval" stems
  to "second," which collides with `cost_latency_pressure`'s evidence term
  "seconds" (a unit-of-time / ordinal-number stemmer collision, not a
  cross-signature vocabulary problem — `cost_latency_pressure` and
  `long_running_process` aren't a registered confusable pair, and
  shouldn't need to be for this). Also pinned as accepted, unfixed.

**Phase 1 — a genuinely blind validation cohort (not yet run).**
See `docs/phase1-validation-authoring-brief.md` — a self-contained brief
with no evidence terms, no synonym choices, and no technical framing,
meant to be handed to an author (ideally an isolated subagent with no
visibility into this conversation or `signatures.yaml`) who has not seen
any tuning decision made in Phase 2 rounds. That's the actual holdout
mechanism: cohort tagging alone doesn't prevent leakage if the same author
who tunes the evidence lists also writes the "held-out" cases.

Recommended scale: **40-60 scenarios**, weighted toward the signatures
that currently have the fewest tuning-cohort examples (03, 06, 07, 08, 09,
11, and the three advisory signatures). See the sample-size reasoning
below for why this range, not a smaller one.

Once authored, add the scenarios to `golden-set.yaml` with
`cohort: validation`, translate the brief's "which problem types apply"
answers into `expected_diagnosis`, and — critically — **do not read their
exact wording while making any Phase 2 evidence-list edit.** If a
validation case is opened to understand *why* it failed, it stops being a
valid holdout for that round; move it to `tuning` and note why in its
`tests` field, the same as any other tuning case.

**Phase 2 — expand evidence lists (tuning cohort only).**
Per signature, source candidate terms from its `problem` text and the
pattern's `summary`/`beats_baseline_when` in `agent_pattern.md`/the
pattern YAML — grounded in the taxonomy's own distinctions, not free
association. Prefer 2-3 word phrases over single words (every collision
found so far — "history," "context across," "breadth," "seconds" — was a
single generic word). Before adding a term: check it against
`CONFUSABLE_PAIRS` for its signature, against `patcomp audit-signatures`,
and against the full tuning-cohort golden set for new cross-firing. Add in
small batches; re-run the full test suite plus `patcomp goldenset
--cohort=tuning` after each batch, tracking recall *and* precision
together. A batch that raises recall but drops precision gets narrowed or
reverted, not shipped.

**Phase 3 — validation checkpoint.**
After a Phase 2 round, run `patcomp goldenset --cohort=validation` **once**
and report the numbers as they come out — no follow-up tuning against a
validation failure in the same round (that turns it into a tuning case and
destroys the holdout for next time). If validation tracks tuning closely,
the round generalised. If there's a real gap, log it as a known issue for
the *next* round and draft a fresh batch of validation cases before the
next checkpoint — don't reuse ones that have now been read.

Hard gate on every round, independent of any other number:
`negatives_false_positive` stays 0 on both cohorts. Never traded off
against recall.

**Phase 4 — stopping rule.**
Stop widening evidence lists once validation recall plateaus across two
consecutive rounds, or validation precision starts dropping, whichever
comes first. At that point the right lever changes — either more
principled synonym sourcing (a real thesaurus pass) or the `ModelDiagnoser`
path (an LLM- or embedding-based second opinion, wired as a *separate*,
disagreement-surfaced signal per the architecture's existing design, never
blended into the deterministic prior's score) — not more manual term
adding into a plateaued deterministic prior.

## How many validation scenarios is "enough"?

Standard sample-size-for-a-proportion math: for a recall estimate with
margin of error `E` at 95% confidence, `n ≈ 1.96² × p(1−p) / E²`.
Worst-case variance (`p=0.5`): `n≈96` for ±10 points, `n≈196` for ±7,
`n≈384` for ±5. That's per signature if a defensible per-signature number
is wanted — impractical across 16 signatures. Realistically: **100-150
well-distributed cases gets a trustworthy aggregate number** (±7-8 points);
**20-30 per signature** is a reasonable floor for the signatures that
matter most, well short of statistical rigor but enough that one case
flipping doesn't swing the number 10+ points the way it still can today
(most signatures have 1-2 tuning-cohort examples; the 4-case
`negative_baseline` set already carries its own explicit caveat in
`report()` for the same reason). The 40-60 recommended for the first Phase
1 batch is a deliberate down payment toward that, not the end state.

## Confusable-pairs registry

Source of truth is `CONFUSABLE_PAIRS` in `src/patcomp/signature_audit.py`.
Add a pair the first time an unintended overlap between it and another
signature is found — the same way golden-set cases get added after a miss
is discovered, not spun up speculatively in advance.
