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

**Phase 1 — a genuinely blind validation cohort (done, 2026-08-01).**
See `docs/phase1-validation-authoring-brief.md` — a self-contained brief
with no evidence terms, no synonym choices, and no technical framing.
Handed to an isolated subagent with no visibility into this conversation,
`signatures.yaml`, or any prior tuning decision (confirmed: it made **zero**
tool calls, i.e. never touched the repository). It authored 45 scenarios;
`expected_diagnosis`/`expected_target`/`expected_outcome` were mechanically
translated from its own stated "which problem types apply" answers
*before* the cohort was ever run — never adjusted afterward. Added to
`golden-set.yaml` with `cohort: validation`.

**One labeling bug found and fixed during integration** (not a system
finding): the build script tagged all 3 of the author's "genuinely
retrieval-only" scenarios as `case_type: positive_single` instead of
`negative_baseline`, which would have hidden a real over-selling event.
Fixed before the checkpoint ran — see `test_original_tuning_cases_are_still_tagged_tuning`
and the corrected `case_type` fields in `golden-set.yaml`.

### Phase 3 checkpoint — first run, 2026-08-01

| Metric | Tuning (26, self-tuned) | Validation (45, blind, never tuned against) |
|---|---|---|
| positive outcome match | 20/20 | 15/36 |
| positive target match | 16/20 | 11/36 |
| diagnosis recall (positives) | **0.938** | **0.231** |
| diagnosis precision (all cases) | 0.962 | 0.819 |
| give-up rate (baseline_fallback) | 0.154 | 0.644 |
| negatives over-sold | 0 | 1 (`policy-coverage-limit-lookup`, pinned as a tracked finding — see `TestGoldenSetValidationCohort`) |

**This is exactly the overfitting signal the whole Phase 0/1 exercise was
built to catch.** Recall drops from 0.938 to 0.231 and the give-up rate
more than quadruples — the tuning-cohort numbers from the two prior rounds
describe how well the evidence lists fit 26 specific examples, not how
well the diagnoser generalises. The dominant failure mode on the
validation cohort is *not* wrong signatures firing (precision only drops
to 0.819) — it's **nothing firing at all**, pushing the case to
`baseline_fallback` (low confidence) rather than a wrong three_cards. That
is the system's own honesty mechanism working as designed on genuinely
unfamiliar phrasing: it under-commits rather than confidently
mis-recommends. But it also means most of these realistic scenarios
currently get "we don't know" instead of a usable recommendation, which is
the actual, now-measured cost of Round 1-2's evidence lists being narrower
than real business phrasing.

Over-fired signatures on the holdout (`cost_latency_pressure` ×4,
`needs_tools_midreasoning` ×3, `multiple_interpretations` ×2,
`weak_judgement` ×2, `stale_facts`/`cross_session_recall`/`stable_high_volume`
×1 each) are the concrete Phase 2 worklist this checkpoint produced — not
fixed in this round, per the rule below.

Once evidence lists are widened in a Phase 2 round against these findings,
**do not re-run this specific 45-case cohort as the checkpoint** — reading
its wording to fix it consumes it as a holdout. Author a fresh validation
batch for the next checkpoint (this file's cohort can move to `tuning` at
that point, since it will have informed edits).

### Phase 2 checkpoint — round 1, 2026-08-01

Evidence lists were widened for 14 of the 16 signatures, sourced only from
each signature's own `problem` text, its pattern's `summary` /
`beats_baseline_when`, and the *aggregate* `over_fired_signatures` counts
from the Phase 1 checkpoint above — never from reading a validation-cohort
case's wording. `metrics()` gained `recall_by_signature` (aggregate
hit/expected counts per signature, never per-case) specifically so this
round could be targeted without opening any of the 45 held-out cases.

Three self-introduced problems were found and fixed before this checkpoint
ran, via the tuning cohort and `patcomp audit-signatures` — not the
validation cohort:
- Removing bare `tool` from `needs_tools_midreasoning` regressed
  `renewals-copilot` (tuning) to `baseline_recommended`, because
  `stale_facts`'s newly-added `look it up` bag-of-words-matched "looking up
  ticket severity" instead. Fixed by dropping `look it up` from
  `stale_facts` and adding `mid-reasoning`/`mid reasoning` (the signature's
  own name) to `needs_tools_midreasoning`.
- `needs_tools_midreasoning`'s new term `call out to` bag-of-words-collided
  with `workflow_too_large`'s own pattern summary ("fan **out**" + "**call**s").
  Removed.
- `needs_tools_midreasoning`'s new term `check live` bag-of-words-collided
  with unrelated "identity **checks**... goes **live**" wording in a
  `long_running_process` tuning case. Removed (redundant with "checking as
  it goes" / "live systems" anyway).

| Metric | Tuning (26, self-tuned) | Validation (45, blind, read only in aggregate) |
|---|---|---|
| positive outcome match | 20/20 | 21/36 (was 15/36) |
| positive target match | 16/20 | 18/36 (was 11/36) |
| diagnosis recall (positives) | 0.938 (unchanged) | **0.454** (was 0.231) |
| diagnosis precision (all cases) | 0.962 (unchanged) | 0.843 (was 0.819) |
| give-up rate (baseline_fallback) | 0.154 (unchanged) | 0.533 (was 0.644) |
| negatives over-sold | 0 | **0** (was 1 — `policy-coverage-limit-lookup` is now correctly diagnosed) |

The tuning cohort held exactly steady — no regression traded for the
validation gain. Validation recall roughly doubled and the give-up rate
dropped by more than a fifth, without spending any visibility into the
holdout's actual wording; precision moved in the right direction too
(0.819 → 0.843), so this wasn't recall bought with false positives.

Per-signature recall on the validation cohort after this round (aggregate
hit/expected — see `recall_by_signature`): `multiple_interpretations` 3/3,
`human_judgement_in_output` 3/3, `stable_high_volume` 2/2,
`cross_session_recall` 2/3, `long_running_process` 2/3,
`workflow_too_large` 2/3, `validated_artefacts` 2/3,
`needs_tools_midreasoning` 1/3, `planning_under_constraints` 1/3,
`should_improve_over_runs` 1/3, `cost_latency_pressure` 1/3,
`weak_judgement` 0/3, `deterministic_policy_compliance` 0/3,
`relationship_discovery` 0/3, `stale_facts` 0/3, `quality_undefined` 0/2.
The five signatures still at 0 are the concrete worklist for a future
round. `cost_latency_pressure` also still over-fires 4 times on the
holdout — removing "per day" didn't fix it, meaning one of the round's
other terms is now the culprit; not yet diagnosed, since finding out which
one requires either aggregate signal this round didn't produce or reading
case text, which would spend the holdout. Both are logged here rather than
chased further in this round, per the "run once, report, stop" rule below.

No validation-cohort case text was read at any point in this round —
only the numbers in the table above and the per-signature counts.

### Phase 2 round 2 — 2026-08-01

Round 1 left five signatures at 0 recall on the holdout and one
still-unexplained over-firer (`cost_latency_pressure` ×4). Continuing to
widen evidence lists by staring at the same 45-case cohort's aggregate
counts a third time would itself have become a slow leak — the numbers
would stop being a genuine generalisation check and start being an
overfit to *this specific 45-case sample*, even without ever reading a
word of it. So this round did two things before touching another
evidence list:

1. **Relabelled the 45 Phase 1 cases `validation` → `tuning`.** Honest,
   since their aggregate signal had already informed two rounds of edits.
   This also made their text fair game to read directly, the same as any
   other tuning case.
2. **Read the now-tuning cases for the five 0-recall signatures and the
   over-firer**, which surfaced two real bugs in `diagnose.py`, not just
   vocabulary gaps:
   - **Self-negating evidence terms cancelled themselves out.** Several
     signatures are *defined* by an absence — `deterministic_policy_compliance`'s
     `no exceptions`, `relationship_discovery`'s `no one filing shows` /
     `no single record`. The negation scanner flagged any segment
     containing "no " as negated, then discounted every term matching
     inside that segment — including the term whose own "no " had
     triggered the flag in the first place. `quality_undefined`'s
     `no scorecard`, `deterministic_policy_compliance`'s `no exceptions`,
     and `relationship_discovery`'s `no one filing shows` / `no single
     record` were all self-cancelling on every single occurrence, not just
     when something else nearby genuinely negated them. Fixed in
     `score_signatures()`: a term whose own text matches the negation
     pattern is now exempt from the negated-clause discount (the term
     itself *is* the positive evidence). Verified this does not weaken the
     original, legitimate case the negation scanner exists for ("It does
     not compare options, plan, or take actions" still correctly
     suppresses `compare options` / `take actions`, since neither of those
     terms is itself negation-shaped).
   - **Em-dashes and trailing ", not X" clauses weren't clause
     boundaries.** "...80 milliseconds to respond per auction... — cost
     and speed are the whole ballgame here, not accuracy" is two
     independent clauses joined by a dash; without splitting there, the
     second clause's "not accuracy" reached back across the dash and
     erased "milliseconds" in the first. The same shape recurred with a
     trailing contrastive aside after a comma: "pick the best one with a
     reason, not run endless split tests" and "a considered call..., not a
     coin flip" both had their own positive evidence erased by a
     negation several words later that was actually negating something
     else. Added `—`/`–` and `,\s+not\b` as segment boundaries in
     `_SEGMENT`. Verified against the existing list-negation test (`does
     not compare options, plan, or take actions`) — unaffected, since
     there the items are comma-separated without "not" directly following
     a comma.

   These two fixes alone resolved 9 of the round's 16 case-level gaps
   before a single evidence-list word was touched.
3. **Widened evidence for the remaining gaps** using both the established
   `problem`/`summary`/`beats_baseline_when` grounding and — now that
   these are legitimate tuning cases — their own wording where a
   generalisable phrase was clearly there (e.g. `weigh it differently` as
   a sibling of the existing `weigh the facts`; `restarts overnight` as a
   plain-English sibling of pattern 08's `survives restarts`).
4. **Removed three evidence terms that had zero true-positive reliance and
   were pure bag-of-words collision generators**: `real time` (the actual
   dominant `cost_latency_pressure` over-firer round 1 left unexplained —
   "real" and "time" are both common enough to scatter-match unrelated
   text like "real cause... last time"), `scale`, and `several steps`.
   Checked reliance by re-scoring every true-positive case for that
   signature with the term removed before deleting it.

**Result: the original 26 held their exact Round-1 baseline** (20/20
outcome match, 16/20 target match, recall 0.938, precision 0.962, 0
false positives) — nothing was traded away. Across the full 71-case
tuning cohort (26 original + 45 relabelled):

| Metric | Full tuning cohort (71) |
|---|---|
| positive outcome match | 56/56 |
| positive target match | 46/56 |
| diagnosis recall (positives) | 0.90 |
| diagnosis precision (all cases) | 0.894 |
| negatives over-sold | 0 |
| give-up rate | 0.141 |

12 cases still fire an unexpected signature; each was investigated
individually rather than removed on sight — see the comments beside
`_KNOWN_OVER_FIRES` in `tests/test_patcomp.py`. Most are genuine
multi-label boundary overlaps the blind author simply didn't list (e.g.
`employee-onboarding-stages` explicitly separates hard-rule steps from
judgement-call steps, the same shape as `expense-report-review`, which
*does* expect both signatures) rather than bugs; two
(`contamination-trace-back`→`validated_artefacts`,
`legacy-codebase-migration`→`human_judgement_in_output`) are coincidental
bag-of-words collisions on terms that are load-bearing elsewhere and were
kept rather than weakened.

A fresh, genuinely blind validation batch was authored for this round's
checkpoint (same brief, a new isolated subagent, zero tool calls, 40
scenarios deliberately spanning different industries than the Phase 1
batch — advertising, agriculture, energy, education, veterinary/animal
research, food safety, patent law, and more — to avoid the checkpoint
quietly re-testing the same vocabulary twice) since the Phase 1 batch is
now spent as a holdout.

### Phase 3 checkpoint — round 2, 2026-08-01

| Metric | Tuning (71) | Validation (40, fresh blind holdout) |
|---|---|---|
| positive outcome match | 56/56 | 16/34 |
| positive target match | 46/56 | 13/34 |
| diagnosis recall (positives) | 0.90 | **0.314** |
| diagnosis precision (all cases) | 0.894 | 0.80 |
| give-up rate (baseline_fallback) | 0.141 | 0.525 |
| negatives over-sold | 0 | **0** |

This is a harder cohort than either the tuning set or the Phase 1
holdout, and the number should be read that way, not as a regression:
it is a *fresh* sample, deliberately drawn from different industries and
phrasing than anything any evidence list has ever been tuned against, so
it is the most honest single number this project has produced yet.
Recall (0.314) sits below Phase 1's post-widening result on its own
holdout (0.454) — expected, since that number was partly informed by
having already looked at that specific cohort's aggregate signal twice;
this cohort has informed nothing. The headline invariant still holds
exactly: **zero cases over-sold orchestration** (`negatives_over_sold =
0`), on 4 genuinely negative cases in this batch. Where the tool is
wrong, it is wrong in the safe direction — under-confident
(`baseline_fallback`) rather than confidently wrong — in every case
checked.

Per-signature recall on this holdout: `cost_latency_pressure` 2/2,
`human_judgement_in_output` 3/4, `multiple_interpretations` 2/3,
`workflow_too_large` 2/3, `relationship_discovery` 2/3,
`cross_session_recall` 1/3, `validated_artefacts` 1/2, and eight
signatures at 0: `needs_tools_midreasoning`, `planning_under_constraints`,
`should_improve_over_runs`, `long_running_process`,
`deterministic_policy_compliance`, `stale_facts`, `weak_judgement`,
`quality_undefined`, `stable_high_volume`. That is a wider and more
varied 0-recall list than Phase 1 produced — a direct measurement of how
much the evidence lists still lean on the specific phrasing of the cases
that have been seen (twice-tuning-cohort or Phase-1-holdout-informed)
rather than the concept those signatures actually name. `cost_latency_pressure`
over-fired twice more, alongside new over-firers `should_improve_over_runs`
(×2) and `cross_session_recall` (×2) — a fresh worklist for whichever
round tackles this next.

No validation-cohort case text was read while producing this report —
only the table and per-signature counts above, per the same discipline
as every prior checkpoint. Per the Phase 3 rule, this round stops here:
no follow-up tuning against this result in the same round. The 40 cases
above become the new tuning-informing baseline only if a future round
explicitly relabels them (with the same relabel-then-fresh-batch
discipline used this round) — until then they remain the holdout.

### Phase 2 round 3 — investigating the 0-recall worklist, 2026-08-01

Requested directly: investigate why 9 signatures scored 0/2-0/3 on the
round 2 holdout. Investigating meant reading that batch's actual case
text, which — per the same reasoning as the round 2 relabel — spends it
as a holdout even though only aggregate numbers had been read before.
Relabelled all 40 cases `validation` → `tuning` before reading anything,
rather than quietly reading a "holdout" that was about to stop being one.

**Two more real bugs, found the same way as round 2's:**

- **Negation scope ran across the whole segment instead of forward from
  the cue.** "During a network outage... needs to check live service
  status... rather than working off a static script" has "rather than"
  negating the static-script alternative; a whole-segment scope wrongly
  erased "check live service status" too, even though it's stated
  *before* the cue, not after it. Same bug explained the still-open
  `press-line-scheduling` gap from round 2 and a `deterministic_policy_compliance`
  miss on `claims-processing-workflow` (the word "deterministic" itself,
  sitting before an unrelated "no" later in the same sentence). Fixed by
  slicing each negated segment from the cue's match position onward
  (`negated_spans()`) instead of returning the whole segment. Verified the
  list-negation case ("does not compare options, plan, or take actions")
  is unaffected, since "does not" sits at the start of its segment.
- This fix alone resolved `turbine-inspection-scheduling`,
  `exam-hall-seating`, and `home-aide-visit-scheduling`'s
  `planning_under_constraints` half — all three use "lots of valid X" /
  "dozens of valid X" phrasing that the existing `many valid` term missed
  purely on the word "many" being absent (separately widened, see below),
  compounded by the whole-segment bug swallowing the signal in some cases.

**Widened evidence for the remaining gaps**, same discipline as round 2
(signature's own `problem`/pattern `summary`/`beats_baseline_when` text
plus, now that these are legitimate tuning cases, their own wording where
a generalisable phrase was clearly there): `can't agree which`
(weak_judgement), `lots of valid`/`dozens of valid` (planning_under_constraints),
`carry forward`/`inform the next`/`same mistakes` (should_improve_over_runs),
`runs for weeks`/`system restart` (long_running_process), `zero tolerance`/
`unbreakable` (deterministic_policy_compliance), `nobody has said`/
`no agreed measure` (quality_undefined), `haven't changed in`/`aren't
going to` (stable_high_volume — same apostrophe-tokenization gap as
`quoted`/`quote`: "hasn't"/"haven't" and "isn't"/"aren't" tokenize to
different words and need listing separately), `pull the answer from`/
`straight out of`/`a lookup against` (stale_facts), `a real person`
(human_judgement_in_output).

**Removed six evidence terms with zero true-positive reliance that were
pure bag-of-words collision generators**, found by re-checking the full
tuning cohort after each addition: `valid combinations`, `recurring`,
`run over run`, `picks up where`, `returning customer`, `sign off`. Two
of these had been silently propping up a *correct outcome for the wrong
reason* — `printer-fleet-triage` (`recurring` → `should_improve_over_runs`,
not the expected `needs_tools_midreasoning`) and `dairy-herd-symptom-review`
(`sign off` → `human_judgement_in_output`, not the expected
`multiple_interpretations`) now correctly fall back to `baseline_fallback`
instead of confidently routing via a signature that was never actually
supported. That is a **precision fix reported as a small recall/outcome-match
drop**, not a regression — see the `positive_outcome_match` floor change
in `tests/test_patcomp.py`, which stopped requiring exact equality for
exactly this reason.

**Result:** all 16 signatures now show real recall on the full 111-case
tuning cohort (worst case 4/5; most in the 75-100% range) — a
qualitative change from round 2's 9 signatures sitting at literal zero.
Original 26 held their exact Round-1 baseline throughout (20/20, recall
0.938, precision 0.962). Full 111-case tuning cohort: 84/90 outcome
match, 71/90 target match, recall 0.864, precision **0.914** (up from
round 2's 0.894, despite the much larger and more varied cohort — the
bug fixes bought recall without spending precision). 0 false positives,
unchanged.

Round 3 was not checked against a fresh blind holdout when it shipped —
every batch authored to date had ended up relabelled tuning. Round 4
closes that gap.

### Phase 3 checkpoint — round 4, 2026-08-01

A fresh 40-case blind batch was authored (new isolated subagent, zero
tool calls, deliberately different industries again — legal services,
agriculture tech, telecom, government/public sector, aerospace, pharma
manufacturing, real estate, sports/media, gaming — explicitly excluding
every concrete scenario used in rounds 1 and 2 so the checkpoint isn't
quietly re-testing the same phrasing a third time). Checkpoint run once,
reported as measured:

| Metric | Tuning (111) | Fresh validation (40, never tuned against) |
|---|---|---|
| positive outcome match | 84/90 | 20/32 |
| positive target match | 71/90 | 13/32 |
| diagnosis recall (positives) | 0.864 | **0.438** |
| diagnosis precision (all cases) | 0.914 | 0.762 |
| give-up rate | 0.171 | 0.425 |
| negatives over-sold | 0 | **0** |

This is a genuinely harder, more varied cohort than any prior one, and
the numbers say so plainly: recall and precision both sit below every
tuning-cohort number and below round 2's holdout numbers too. The
structural bug fixes from rounds 2 and 3 (self-negating-term exemption,
forward-scoped negation) are real and verified against every case they
were designed for, but they don't substitute for evidence-list coverage
on genuinely new industries and phrasing — this checkpoint measures that
gap directly rather than assuming the fixes closed it. The hard
invariant still holds exactly: **zero cases over-sold orchestration** on
4 negative cases in this batch.

Per-signature recall on this holdout: `multiple_interpretations` 3/3,
`cross_session_recall` 2/2, `should_improve_over_runs` 2/2,
`cost_latency_pressure` 2/2, `stable_high_volume` 1/1,
`relationship_discovery` 2/3, `deterministic_policy_compliance` 1/3,
`workflow_too_large` 1/3, `validated_artefacts` 1/3, `weak_judgement`
1/3, `stale_facts` 1/4, and five signatures at 0:
`needs_tools_midreasoning`, `planning_under_constraints`,
`long_running_process`, `human_judgement_in_output`, `quality_undefined`.
Over-firing is broader than any prior round too: `stale_facts` ×3, `weak_judgement` ×3,
`cost_latency_pressure` ×2, `needs_tools_midreasoning` ×2, plus four
signatures at ×1 each.

No validation-cohort case text was read while producing this report —
only the table and per-signature counts above, per the same discipline
as every prior checkpoint. Per the Phase 3 rule, this round stops here:
no follow-up tuning against this result in the same round. The honest
reading: the evidence-list approach generalises well to industries and
phrasing similar to what's already been seen (tuning cohort, 0.914
precision) but has real, measured limits on genuinely fresh territory
(0.762 precision, 0.438 recall here) — the gap Phase 4's stopping-rule
alternatives (a real thesaurus pass, or a separate embedding/model
second opinion) exist to eventually address, not more manual term
chasing against an ever-larger tuning cohort.

### Phase 2 round 5 — extending the signature list, 2026-08-01

Requested directly: extend the signature list to improve recall.
Extending it meant reading the round 4 batch's text, so it was relabelled
`validation` → `tuning` first, same discipline as every prior round.

**A fourth real bug, and probably the most consequential one found so
far: contraction-based negation was never working at all.** `_NEG`'s
patterns were written as `"doesn t"` / `"don t"` — spelled the way
`normalise()` renders a contraction (apostrophes aren't alphanumeric, so
`_WORD` tokenisation splits `"doesn't"` into `"doesn"` + `"t"`) — but
`negated_spans()` was matching them against the RAW, un-normalised
segment, where the literal string `"doesn't"` (with an apostrophe) never
equals the pattern `"doesn t"` (with a space). The two spelled-out forms
(`"does not"`, `"do not"`) worked throughout every prior round; every
contraction — `"doesn't"`, `"shouldn't"`, `"can't"`, all of them — was
silently invisible to the scanner from the day it was written. Confirmed
directly: `negated_spans("It doesn't compare options...")` returned `[]`
where `negated_spans("It does not compare options...")` correctly
returned the negated segment. Concretely, `"...are pure lookups against
our policy system and shouldn't involve any judgment at all, human or
otherwise"` let `human approval on` fire as if the sentence never said
`"shouldn't"`. Fixed by normalising each segment before running `_NEG`
against it (`negated_spans()`), and widened the pattern list to cover
the contractions that now actually match (`isn t`, `aren t`, `wasn t`,
`weren t`, `haven t`, `hasn t`, `hadn t`, `won t`, `wouldn t`, `shouldn
t`, `couldn t`, `can t`, `mustn t`, `shan t`, `didn t`). Verified against
every existing negation test, including the list case (`"does not
compare options, plan, or take actions"`) and the contraction form of
the same sentence.

One related, smaller finding logged but not fixed this round: the
round-2 `", not X"` segment boundary (added to stop a trailing aside from
reaching backward) uses a non-capturing regex group, so `re.split()`
consumes the word `"not"` itself along with the comma — the resulting
trailing segment is never actually self-tagged as negated. This doesn't
undermine the boundary's actual purpose (protecting the earlier clause
from cross-clause reach still works), and no case currently depends on
the trailing segment being self-negated, so it's left as a documented
architectural quirk rather than chased further this round.

**Widened evidence for the round 4 batch's remaining gaps**, same
discipline as every prior round (signature's own `problem`/pattern
`summary`/`beats_baseline_when` text plus, now that these are tuning
cases, their own wording): `why the others were rejected` / `nobody can
articulate` (weak_judgement); `cross reference` / `pull live` / `live
status` (needs_tools_midreasoning); `valid orderings` / `knock most of
them out` / `eliminate most` (planning_under_constraints); `sit open
for` / `goes down overnight` (long_running_process); `a record of why` /
`actually sign off` / `a documented reason` / `needs a paper trail`
(human_judgement_in_output); `no discretion` (deterministic_policy_compliance);
`split by area` / `independent pieces` (workflow_too_large); `run
against` / `fails a check` (validated_artefacts); `in measurable terms`
(quality_undefined); `cited` / `read back` / `version number`
(stale_facts); `hops away` (relationship_discovery).

`actually sign off` is a deliberate 3-word-safe replacement for round
3's removed `sign off`: requiring `"actually"` adjacent-in-bag-of-words
recovers the genuine KYC/sign-off signal round 3 traded away
(`beneficial-ownership-check`) without reintroducing round 3's specific
collision (the veterinary case that broke it never says `"actually"`).
It does introduce two new, milder collisions on cases that separately
happen to say `"actually"` elsewhere in a long paragraph — investigated
and pinned as plausible genuine secondary labels (a named `"final
sign-off"` stage inside a larger workflow is boundary-shaped the same
way `expense-report-review`/`disability-benefits-application-flow`
already are), not silently accepted.

**One term added and then removed in the same round**: `maintenance
windows` (long_running_process) fixed nothing on its own (the case it
was added for already fires via `sit open for`) and bag-of-words-collided
on an unrelated "unlogged maintenance event... around the same window" in
a root-cause-investigation case. Checked reliance before removing, same
discipline as every removal this project has made.

**One finding classified as a genuine architectural limitation, not a
term problem**: `hailstorm-claim-multistage`'s `human approval on`
over-fire survives even the contraction-negation fix, because its
bag-of-words support is split across two segments — `"human"` comes from
the correctly-negated `"shouldn't involve any judgment... human or
otherwise"` clause, `"approval"` from an earlier, un-negated `"payout
approval"` — and the per-segment discount only catches a term when ALL
of its supporting words sit inside the same negated segment. The term is
solely load-bearing for `dotnet-migration-synthesis` and can't be
removed. Fixing this properly needs position-aware matching (which
segment did each matched word actually come from), a bigger change than
this round's scope; logged as a known limitation rather than patched
around.

**Result:** all 16 signatures now sit at recall ≥5/6 (83%) on the full
151-case tuning cohort, most in the 85-100% range — `human_judgement_in_output`
15/17, `stale_facts` 12/14, `weak_judgement` 10/13,
`deterministic_policy_compliance` 10/13, `cost_latency_pressure` 8/8,
`stable_high_volume` 5/5. Original 26 held their exact Round-1 baseline
throughout (20/20, recall 0.938, precision 0.962). Full 151-case tuning
cohort: 114/122 outcome match, 97/122 target match, recall 0.871,
precision 0.881, 0 false positives.

This round has not been checked against a fresh blind holdout — no
validation-cohort case currently exists (every batch authored so far has
ended up relabelled tuning). The contraction-negation fix in particular
is structural and should generalise broadly — it isn't phrased around
any case's specific wording, and negation via contraction is at least as
common in real business writing as the spelled-out form — but, as
always, that's a prediction until measured.

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
