# Phase 1 authoring brief — validation-cohort golden-set scenarios

**Hand this file to the author, and nothing else from this repository.**

If you are an AI agent or a person who has read `catalogue/signatures.yaml`,
`agent_pattern.md`'s "how it differs from the alternatives" sections, or any
prior round's evidence-term choices — **stop and use a different author.**
This document exists specifically so the scenarios it produces are a genuine
held-out test, not something tuned toward. Anyone who has seen the exact
matching vocabulary will unconsciously write scenarios that echo it, which
defeats the purpose. Everything you need is below; you should not need to
open any other file in this repository to do this task.

## What you're building

A tool reads a short written description of a business problem and
recommends an AI-agent architecture for it. Below are 16 kinds of problem it
can recognise, each with a one-line description of the problem and what a
good solution for it looks like. Your job: write realistic scenario
descriptions — the way an actual business person would describe their
problem to a consultant, not the way a specification would describe it — one
(or, for the "compound" scenarios described below, several) of these
problems.

**Do not use the id names, the technical descriptions, or any single
distinctive word from them.** If a signature's description says
"deterministic," don't write "deterministic" — write what a compliance
officer or a claims manager would actually say. Imagine describing this
verbally to a colleague, not filling in a form.

## The 16 problem types

1. **weak_judgement** — Correct facts, weak judgement. Good solution: generate candidate answers, evaluate them against criteria, pick one and say why the others lost. Wins over doing nothing extra when: the facts are already available and the failure is in judgement, not retrieval.
2. **needs_tools_midreasoning** — Needs tools, data lookups or actions mid-reasoning. Good solution: think, act, observe, repeat under a budget, with tools and a knowledge base. Wins when: the answer requires live lookups or actions that can't be looked up in advance.
3. **planning_under_constraints** — Planning under constraints with many valid options. Good solution: cheap breadth first, a free hard-constraint filter, then depth on the survivors. Wins when: many candidate plans are valid, hard constraints kill most of them cheaply, and the ordering matters.
4. **multiple_interpretations** — Multiple plausible interpretations, first answer often wrong. Good solution: hold several hypotheses at once, let evidence prune them, synthesise a verdict from the survivor. Wins when: the first plausible explanation is often wrong and being wrong is expensive.
5. **cross_session_recall** — Needs recall across sessions, personalised or accumulating context. Good solution: scoped, security-trimmed memory with an expiry. Wins when: quality depends on what happened in earlier sessions, not just this one.
6. **should_improve_over_runs** — A repeated task that should get better run over run. Good solution: reflect on a completed run, write down a reusable lesson, put it through review, apply it next run. Wins when: the same task recurs often enough that capturing a lesson pays for itself.
7. **long_running_process** — A long-running business process with defined stages. Good solution: a durable process that survives restarts, with named stages, some of which are pure rules with no AI involved at all. Wins when: the process spans hours or days, must survive a restart, and some steps must never involve a model.
8. **workflow_too_large** — A job too big or too varied for one agent. Good solution: a planner splits it up, small-model workers handle each piece in parallel, an independent reviewer checks the combined result. Wins when: the work splits into 2-4 narrow, independent sub-tasks.
9. **human_judgement_in_output** — A human's judgement is part of what makes the output good, not just a rubber stamp. Good solution: a named point where a person reviews, with a time limit, an escalation path, and a recorded reason. Wins when: getting it wrong is expensive enough to be worth someone's attention.
10. **deterministic_policy_compliance** — A policy or rule must hold every single time, not just usually. Good solution: the model proposes, a deterministic rules engine decides, and the engine's decision is final. Wins when: "usually right" isn't good enough — it has to be guaranteed.
11. **relationship_discovery** — The answer lives in the relationships between records, not in any single document. Good solution: walk an entity graph and cite the chain of evidence followed. Wins when: no single record contains the answer.
12. **validated_artefacts** — The deliverable is a working artefact (code, a migration, a document that must pass a check), not advice. Good solution: generate it, test it, if it fails analyse why and repair it, repeat up to a cap. Wins when: there's an objective pass/fail check available, so nothing has to take the system's word for it.
13. **quality_undefined** *(advisory — flags a gap, doesn't pick an architecture)* — Nobody can currently say what a "good" output looks like or how you'd measure it.
14. **cost_latency_pressure** *(advisory)* — This runs at real scale, and cost per run or how fast it must respond is a real constraint, not an afterthought.
15. **stable_high_volume** *(advisory)* — A stable, unchanging, very high-volume workload where the overhead of orchestration itself becomes the dominant cost.
16. **(a case that shouldn't match any of the above)** — A problem that's really just "find the right document and answer with a citation" — no comparison, no planning, no verification, nothing that needs judgement. See "Negative and messy cases" below.

## Diversity requirements — this is the actual point of the exercise

Write **{{N}} scenarios total**, distributed roughly evenly across the
15 pattern-bearing types above (fewer per case is fine for the 3 advisory
ones — 1-2 each is enough), varying deliberately along these axes. Don't
write all {{N}} in the same style — mix it up scenario by scenario:

- **Length.** Some should be one or two plain sentences. Some should be a
  denser paragraph, the way a real internal memo or ticket would read. At
  least a few should be genuinely terse (under 20 words).
- **Structure.** Most should be plain prose. A few can look like a pasted
  chunk of a requirements doc or a bulleted list with irrelevant boilerplate
  mixed in (a header, a ticket number, a "priority: high" line that has
  nothing to do with the actual problem).
- **Clarity.** Most should clearly describe one problem type. Deliberately
  include a handful that are genuinely ambiguous or under-specified — where
  a reasonable answer might be "we don't have enough information yet," not a
  confident recommendation. See "Negative and messy cases" below — these are
  not failures if the tool correctly declines to guess.
- **Compound scenarios.** Include a few scenarios that genuinely combine two
  or three of the problem types above in one realistic situation (the way a
  real business process usually isn't just one clean problem). Note ALL the
  problem types that genuinely apply for these.
- **Voice and vocabulary.** Vary industry (finance, healthcare, retail,
  manufacturing, internal IT, HR, logistics...) and vary how formal or
  colloquial the writing is. Some should read like they were written by a
  non-native English speaker, or dashed off quickly, not polished prose.

## Negative and messy cases (include some deliberately)

Include a handful of scenarios that are **genuinely** just retrieval /
"find the document and cite it" with no reasoning, judgement, planning, or
verification involved — these test that the tool doesn't over-recommend
machinery where none is needed.

Also include a few that are **too vague to diagnose** — missing what a
good outcome even looks like, or what decisions are involved, or any
detail at all beyond "help us be more efficient." These should be marked
as expecting *no* confident recommendation, not forced into matching one of
the 16 types above.

## What to hand back for each scenario

For each scenario, give:
1. A short id/title.
2. The scenario text itself.
3. Which of the 16 problem types genuinely apply (can be more than one, or
   none for the negative/vague cases).
4. Whether you'd expect a confident recommendation at all, or whether this
   scenario is realistically too vague/ambiguous to diagnose.

Do not attempt to map your scenarios onto any specific technical solution,
composition, or architecture — that's the tool's job to determine, and
seeing you attempt it would itself be a sign you've inferred the technical
framing this brief is trying to keep you blind to. Just describe realistic
problems and say which category of problem (by number, from the list above)
each one is.
