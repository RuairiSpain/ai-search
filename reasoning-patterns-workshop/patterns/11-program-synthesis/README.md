# Pattern 11 — Program Synthesis & Test-Repair (white paper §16)

Code generation produces code that looks correct; synthesis produces artefacts
**validated against tests**. The deliverable here is a green pytest run plus
the diff — and two structural guarantees that "green" means the code moved,
not the goalposts.

Diagram: [ARCHITECTURE.md](ARCHITECTURE.md) · Healthy run: [data/expected_output.md](data/expected_output.md)

Deploy/run/eval/traces/teardown mechanics common to every pattern: [HOW-TO-RUN.md](../../HOW-TO-RUN.md).

## 1. Deploy

```bash
make deploy
```

Installs pytest and runs a sanity check with teeth: the acceptance suite is
executed against the *legacy* module and must FAIL — proving the migration has
real work to do and the tests aren't vacuous.

## 2. Run

```bash
make run
```

Watch the loop: generate `config.py` → pytest in an isolated temp workspace →
failure analyst reads tracebacks → minimal repair → revalidate, up to 3
rounds. Deliverables land in `runs/`: the migrated module and a unified diff
against the legacy source.

Two guarantees hold no matter what any model outputs: the harness only ever
writes model output to `config.py` (it structurally cannot touch tests), and
test files are sha256-checksummed every round. This is §14's
tendency-vs-guarantee argument reappearing inside §16 — say it out loud.

## 3. The experiments

```bash
make eval                       # full loop, full suite
make eval VARIANT=no-repair     # one shot — first-attempt pass rate
make eval VARIANT=frontier      # does the big model need fewer rounds?
make run  VARIANT=weak-tests    # green pipeline on 3 of 8 tests…
make verify-full                # …then the FULL suite over that output
```

- **no-repair vs baseline** puts a number on the repair loop's value.
- **weak-tests + verify-full** is the session's best moment: a green pipeline,
  then the full suite exposing what it shipped. The §16 CSA prompt — *"is the
  test suite strong enough that passing it means something?"* — answered
  empirically on the projector.
- **Row p11-03** pressures the loop to edit tests under deadline; the correct
  response is a refusal (and the harness makes compliance impossible anyway —
  defence-in-depth: instruction, structure, checksum, eval).
- The primary evaluator is **pytest itself** — `tests_passed` rides in the
  trace, and the graders are told to trust it over the prose. Model judges
  judge what tests can't: honesty, scope, tamper-refusal.

Model-generated `config.py` executes inside `reasoning_common.sandbox`: no
network, no process spawn, secrets stripped from the environment, resource
ceilings. See SECURITY.md for exactly what that does and doesn't guarantee.

## 4. Traces

App Insights → service `pattern-11-synthesis`: `p11.generate` → `p11.test` →
`p11.analyze` per round. The round count per row is the metric to compare
across model variants.

## 5. Production shape

Same loop, different substrate: generation via GitHub Copilot coding agent or
codex-class Foundry models, validation via GitHub Actions / Azure DevOps,
GitHub Advanced Security scanning as an extra gate, human approval on
high-risk changes, every change linked to requirement + tests + approval
(§16 production controls). The workspace-and-checksum discipline here is the
miniature of that pipeline.
