# T1 sample 03 — code interpreter + shared-memory ELI5 flashcards

| | |
|---|---|
| Tier | **T1** (two prompt agents) — not fronted by this gateway |
| Stability | **Mixed.** Code interpreter and prompt agents are GA; the `memory` service this sample uses for the formatter is preview (D10) |
| RBAC | Same as sample 01 — `Azure AI Developer` on the project to deploy |
| Region features | Code interpreter must be available in-region for the calculator's model deployment (D9/L011) |

## What this shows

Two agents again, same executor→consumer shape as
`../08-workflow-executor-reviewer` — but the opposite design choice on the
one axis that sample exists to demonstrate. The **calculator** agent uses
code interpreter to actually run the arithmetic in the user's prompt (never
trusting the model to do long division in its head). The **flashcard
formatter** agent takes the calculator's result and turns it into an ELI5
preschool-level multiple-choice math flashcard — and it does this **with
full access to the calculator's conversation and memory**, not a cold
handoff.

```
calculator.yaml  ──same conversation, same memory scope──▶  flashcard-formatter.yaml
   (code_interpreter runs the actual math)                     (sees the full exchange:
                                                                  the user's original
                                                                  prompt, the calculator's
                                                                  work, AND anything in
                                                                  per-user memory from
                                                                  earlier sessions)
```

**Direct contrast with `../08-workflow-executor-reviewer`:** that sample's
reviewer gets a fresh, empty conversation every call and sees nothing but
the current draft. This sample's formatter gets the *same*
`conversation_id` the calculator used, plus a declared `memory` scope
(`session`, `user`), so it inherits everything: the raw user prompt, the
calculator's intermediate reasoning, and per-user memory across sessions
("this user has seen easy addition cards before, so today's card can be
slightly harder"). Read both READMEs side by side — same two-agent
skeleton, opposite answer to "does agent 2 see agent 1's history."

## Why the formatter needs shared memory here (and the reviewer didn't)

The reviewer in sample 08 is explicitly judging output quality in
isolation — that's the value of a cold read. The formatter here is doing
something structurally different: it's *personalizing presentation* of a
result the calculator already validated, and personalization is exactly
the case memory exists for (`docs/02-decisions.md` D2: "session memory is
safe to use now... scoped to a conversation"). There's no isolation value
in hiding the original prompt or prior turns from a step whose whole job is
to adapt to them.

## Structured output

The formatter's `outputSchema` (`agents/flashcard-formatter.yaml`) enforces
the flashcard shape — `question`, four `choices`, `correct_index`, and an
ELI5 `explanation` — so the result is renderable by a UI without prompt-string
parsing. This is a structured-output contract in the general sense
(`docs/02-decisions.md` D4 mentions the same mechanism for the narrower
`needs_input` case); this agent never asks a clarifying question, so its
schema has no `status`/`needs_input` fields — see sample 01's README for
why an unused `input-required` path is dead schema, not a nice-to-have.

## Structure

```
03-code-interpreter-shared-memory/
├── azure.yaml
├── agents/
│   ├── calculator.yaml / calculator.instructions.md          # code_interpreter
│   └── flashcard-formatter.yaml / flashcard-formatter.instructions.md  # outputSchema + memory
├── workflows/
│   └── calc-to-flashcard.workflow.yaml   # both steps share conversationId, on purpose
├── scripts/
│   ├── deploy.py
│   ├── run.py           # direct-SDK driver, same conversation for both calls
│   └── teardown.py
└── requirements.txt
```

## Run it

```bash
export FOUNDRY_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
export FOUNDRY_MODEL_DEPLOYMENT=<your deployment name>
az login

pip install -r requirements.txt
python scripts/deploy.py
python scripts/run.py "What is 847 divided by 11, rounded to 2 decimal places?"
python scripts/teardown.py
```

Expect JSON back, e.g.:

```json
{
  "question": "If you split 847 cookies evenly among 11 friends, about how many cookies does each friend get?",
  "choices": ["77.00", "76.09", "85.00", "70.58"],
  "correct_index": 1,
  "explanation": "847 cookies shared by 11 friends: each friend gets about 77 cookies, with a little bit left over -- so the exact answer is 76.09!"
}
```

## The deliberate failure path

```bash
python scripts/run.py "What is 12 divided by 0?"
```

Division by zero isn't a math answer code interpreter can produce, and the
formatter's job when the calculator can't produce a number is to say so
rather than invent a plausible-looking flashcard around a number that
doesn't exist. `calculator.instructions.md` requires the agent to state the
failure explicitly instead of hand-waving a result, and
`flashcard-formatter.instructions.md` requires the formatter to detect that
and return `{"question": null, "explanation": "<why this can't be a flashcard>", ...}`
rather than fabricate `choices`/`correct_index` around nothing. `run.py`
prints this path distinctly so it's obvious the failure was handled, not
mishandled silently.
