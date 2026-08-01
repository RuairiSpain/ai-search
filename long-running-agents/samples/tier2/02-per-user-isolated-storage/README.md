# T2 sample 02 — per-user isolated storage + artifact harvest

| | |
|---|---|
| Tier | **T2** (hosted agent), fronted by this gateway |
| Stability | Hosted agents are preview end to end (`Foundry-Features: HostedAgents=V1Preview`, docs/05 §3, D10); code interpreter is GA |
| RBAC | Gateway identity needs `UserIdentityImpersonation` on the agent — **this sample's entire premise breaks silently without it**, see "The deliberate failure path" below |
| Region features | Hosted-agent region + code-interpreter availability for the model deployment (docs/05 §1) |

## What this shows

**One hosted agent, three simulated users, calling it through the same
gateway app.** Two things, deliberately kept separate so each is easy to
verify on its own:

1. **Per-user `$HOME` isolation.** Every turn, the agent calls a function
   tool that appends the user's message to a JSON-lines file at a *fixed*
   path — `Path.home() / "session_notes.jsonl"` — and returns how many
   notes are now there. Same code, same path, on every single call. What
   differs is *which* `$HOME` that path resolves inside, because T2's
   per-user delegation (`x-ms-user-identity`, docs/00 §5) puts each
   principal in their own VM-isolated sandbox. Alice's second message
   should see a note count of 2; Bob's first message, sent in between,
   should see a note count of 1 — not 3.
2. **Artifacts outliving the agent.** The same turn also uses code
   interpreter to write the user's prompt into a real `.docx` file. The
   gateway's existing harvest pipeline — `_new_artifacts()` detecting the
   `container_file_citation`, `ArtifactHarvester` copying the bytes into
   the shared blob container before the ~1h container TTL — picks it up
   with **no new gateway code**; this sample only exercises machinery
   already built and tested (`docs/07-artifacts-and-code-interpreter.md`
   §2). The client polls `GetTask` and reads the download link straight
   off `task.artifacts[].parts[].url` — a fresh, short-lived SAS the
   gateway mints on every read (`GatewayTaskStoreAdapter._project_artifacts()`,
   `src/gateway/a2a_server/task_store.py`).

## Two different sandboxes, one story

Worth being precise about, since it's easy to conflate: the `$HOME`
demo and the artifact demo exercise **two different subsystems** inside
what a user experiences as "the hosted agent."

| | `$HOME` (isolation demo) | code interpreter container (artifact demo) |
|---|---|---|
| Mechanism | function tool (`@ai_function`) — agent-author's own pre-written Python | code interpreter — model-written Python, executed by the platform |
| Runs where | the agent's own hosted-session container | a separate, ephemeral sandboxed container (docs/07 §3) |
| Lifecycle | ~30 days inactivity, 15-min idle cold start (docs/00 §5) | ~1 hour, refreshed by any container operation (docs/07 §3) |
| Isolation unit | the delegated identity's session (`x-ms-user-identity`) | the same per-user session context |

Both still ultimately trace back to the same per-user delegation — a code
interpreter container attached to Alice's session never sees Bob's files
either — but they're not the same thing, and this sample is designed so
you can see each mechanism do its own job rather than one demo standing in
for both.

## Structure

```
02-per-user-isolated-storage/
├── README.md
├── agent/
│   ├── main.py              # protocol host + both tools wired in
│   ├── instructions.md      # the exact docx-writing recipe the model runs verbatim
│   └── requirements.txt
├── apps.yaml.snippet.yaml
└── client/
    ├── fake_chat_ui.py       # drives Alice, Bob, Carol -- interleaved, not sequential
    └── requirements.txt
```

## Why the docx-writing code is fully spelled out in `instructions.md`

Code interpreter often answers in prose instead of producing a file unless
told very explicitly (docs/07 §5: "If no file comes back, rephrase the
prompt to explicitly request file output"). Rather than hope the model
improvises correct `.docx` XML from a one-line instruction — a real risk,
since a `.docx` is a zip of several interdependent XML parts and a subtly
wrong one produces a file Word refuses to open — `instructions.md` gives
the model the **complete, tested** `zipfile`-only (no `python-docx`, which
isn't installed in the sandbox) function to run verbatim, substituting
only the prompt text and turn number. This exact function was written and
verified in this sample's own development: built, round-tripped through
`python-docx.Document()` to confirm Word can actually open it and read the
paragraphs back correctly, *before* being pasted into the instructions —
not assumed to work.

## Wire it into the gateway

```yaml
# apps.yaml.snippet.yaml
apps:
  - name: isolated-storage-t2
    tier: t2
    upstream: isolated-storage-t2-hosted
    default_mode: long
    preview: allow

upstreams:
  - id: isolated-storage-t2-hosted
    tier: t2
    project_endpoint: ${FOUNDRY_PROJECT_ENDPOINT}
    agent_name: isolated-storage-t2
    identity: per_user      # <- the whole sample depends on this; see below
```

`identity: per_user` is what makes `FoundryHostedAdapter._headers()`
(`src/gateway/upstream/foundry_hosted.py`) send `x-ms-user-identity:
<principal.subject>` on every call — the gateway does this automatically
for every T2 app configured this way; nothing in this sample's own code
sends that header. It's the platform's isolation unit (docs/00 §5 "Tier 2
identity — the trap"): without it, every caller collapses into one shared
sandbox and this sample's whole premise silently stops being true (the
`$HOME` note counts would interleave across all three "users").

## Getting three real users

D1 and the platform's own isolation both key off the *verified* token's
`oid` claim — there's no dev-mode bypass in `EntraValidator`
(`src/gateway/auth/principal.py`), and there shouldn't be, since the same
validation is what makes per-user delegation trustworthy in production.
So this sample genuinely needs three distinct Entra identities, not three
fabricated ones:

```bash
# One-time, in a dev/test tenant: three throwaway test accounts, or reuse
# three colleagues' accounts. Real Entra registration is out of scope for
# this sample -- assume they already exist.
az login --username alice@yourtenant.onmicrosoft.com
export ALICE_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
az login --username bob@yourtenant.onmicrosoft.com
export BOB_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
az login --username carol@yourtenant.onmicrosoft.com
export CAROL_TOKEN=$(az account get-access-token --resource api://a2a-gateway --query accessToken -o tsv)
```

## Run it

```bash
export GATEWAY_URL=http://localhost:8080
pip install -r client/requirements.txt
python client/fake_chat_ui.py
```

`fake_chat_ui.py` deliberately **interleaves** the three users rather than
running each to completion before starting the next — Alice, Bob, Carol,
Alice again, Bob again — specifically so a bug that leaked state between
sessions would show up as a wrong note count, not be hidden by accidental
serialization. For each turn it prints the model's own reply (which states
the note count in whatever words it chooses — the instructions ask it to,
they don't hand it a template) and, once the task completes and the
artifact is harvested, the download link — then actually downloads each
file and prints its paragraphs back (via `python-docx`) so you can see with
your own eyes that Alice's file contains only Alice's prompts.

Reading the reply text back at all depends on a second fix this sample's
build surfaced: `StatusEvent.detail` on a *completed* task used to stay on
`_narrate()`'s generic "drafting a response" placeholder forever, never the
agent's actual answer — see `docs/08-open-items-and-experiments.md` item 17
and `src/gateway/upstream/foundry_responses.py`'s `_detail_for()`.

Expected shape of the output (three interleaved users, note counts never
crossing between them — exact wording is the model's own, this is
illustrative):

```
[alice] turn 1: You now have 1 note recorded in your session. Your Word document is on its way.
           artifact: https://<storage-account>.blob.core.windows.net/artifacts/...&sig=...
           downloads/alice_turn_1.docx: ['Prompt, turn 1', 'My favorite color is blue.', ...]
[bob]   turn 1: This is your 1st message this session...
           artifact: https://...
[alice] turn 2: You've now sent 2 messages in this session...
           artifact: https://...
[carol] turn 1: ...
[bob]   turn 2: You now have 2 notes recorded...
[carol] turn 2: ...

Expected: alice ends at turn 2, bob at turn 2, carol at turn 2 --
each strictly counting only their own messages, regardless of interleaving.
```

## The deliberate failure path

```bash
python client/fake_chat_ui.py --identity-mode service
```

Points the client at a second upstream entry you add with `identity:
service` instead of `per_user` (see `apps.yaml.snippet.yaml`'s comment).
With `identity: service`, `FoundryHostedAdapter._headers()` never sends
`x-ms-user-identity` at all, so every caller — Alice, Bob, Carol — lands in
the *same* underlying sandbox. Run this mode and watch the note counts:
Alice's second turn now reports 3 or 4 notes, not 2, because Bob's and
Carol's messages landed in the same `$HOME` file hers did. This is the
whole isolation story failing on purpose, so it's visible rather than
theoretical — the exact trap `docs/00-tier-model-and-concepts.md` §"Tier 2
identity — the trap" warns about.
