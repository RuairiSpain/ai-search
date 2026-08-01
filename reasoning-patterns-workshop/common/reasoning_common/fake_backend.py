"""A synthetic, offline fake backend for Foundry chat/run_agent/upsert_agent
and MCP tool calls — no live Azure endpoint, no network, no cost.

This closes the gap between "the modules import" and "the patterns work"
(project review, item 20): every pattern's `run_case()` requires a live
Foundry project today, so nothing exercises the actual control-loop logic —
deterministic checks, pydantic validation, routing, budgets, sandboxing,
MCP dispatch — until real money is being spent. `scripts/run_ci_smoke.py`
uses this module to drive all eleven patterns' `run_case()` in a few
seconds, suitable for an actual CI job.

What this is NOT: a recorded-cassette replay of real API responses. No live
credentials were ever available in this environment to record from. Instead:

  - `chat`/`chat_json` read each prompt's OWN documented output contract
    (via schema_sniffer, which parses the exact schema text every
    instructions.md file already shows the model) and synthesize a
    structurally valid fake response. Two prompts don't fit that generic
    shape and are special-cased below: pattern 04's field-list extractor
    prompt, and pattern 11's "output raw Python source" patch generator
    (which gets a real, working reference implementation, so pattern 11's
    genuine pytest evaluator can actually pass against it).
  - `run_agent` (patterns 02/06, which use the Agent Service thread/run
    surface instead of raw chat) returns a plausible transcript whose
    `steps` mention the tools that pattern's agent is allowed to call, so
    `_extract_tool_calls`/`_semantic_recall_invoked` have something real to
    detect.
  - `call_mcp_tool` dispatches to the ACTUAL route-handler functions in
    `common/mcp_server/routes/*.py` — the same pure-Python, synthetic-data
    functions the real MCP server runs, just called in-process instead of
    over HTTP. No duplicated fake data to drift out of sync.
  - `shield_observations` recognizes the specific injection strings planted
    in this repo's fixtures, so the injection-detection code path is
    exercised meaningfully rather than trivially returning "safe" always.

Every pattern that calls `call_mcp_tool` imports it by direct name
(`from reasoning_common.mcp_client import call_mcp_tool`), which copies the
reference at import time — patching `mcp_client.call_mcp_tool` afterward
would NOT reach those already-bound names. `install_into(module)` patches a
specific pattern module's own attribute; `install()` patches the shared
`foundry_client`/`mcp_client`/`safety` module objects for anything that
looks them up freshly (chat/chat_json/run_agent/upsert_agent are looked up
as `fc.xxx` attribute access in every pattern, so patching the shared module
is sufficient for those).
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

from . import foundry_client as fc
from . import mcp_client
from . import safety
from . import schema_sniffer as ss

REPO_ROOT = Path(__file__).resolve().parents[2]

INJECTION_MARKERS = (
    "IGNORE ALL PREVIOUS INSTRUCTIONS",
    "mark this grant as",
    "ignore dependency rules",
    "approve all accounts using this device",
)

PATCH_GENERATOR_REFERENCE = '''\
_SENTINEL = object()


def _typed(v):
    v = v.strip()
    if v.lower() == "true":
        return True
    if v.lower() == "false":
        return False
    try:
        return int(v)
    except ValueError:
        return v


def parse_config(text):
    result = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"malformed line: {line!r}")
        k, v = line.split("=", 1)
        result[k.strip()] = _typed(v)
    return result


def get(config, key, default=_SENTINEL):
    if key in config:
        return config[key]
    if default is not _SENTINEL:
        return default
    raise KeyError(key)
'''


def _system_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages if m.get("role") in ("system", "developer"))


def _combined_text(messages: list[dict]) -> str:
    return "\n".join(str(m.get("content", "")) for m in messages)


def fake_chat(deployment, messages, *, temperature=None, response_format=None,
              max_output_tokens=None):
    text = _combined_text(messages)
    system_text = _system_text(messages)

    if "Output ONLY JSON with keys risk_score, pep, exposure_eur" in text:
        payload = {"risk_score": 55, "pep": False, "exposure_eur": 40000,
                   "jurisdiction": "PT", "id_verified": True, "missing_fields": []}
        out = json.dumps(payload)
    elif "Output ONLY the full Python source of config.py" in text:
        out = PATCH_GENERATOR_REFERENCE
    elif response_format:
        # The schema is documented in the SYSTEM prompt (every instructions.md
        # file's convention). Search there FIRST: a USER message can contain
        # its own JSON-shaped content (a tool observation, an upstream
        # worker's output) that looks exactly like a schema block to a naive
        # "find the last {...}" scan — found by testing pattern 03's worker
        # role, whose user message carries an MCP tool observation that was
        # getting mistaken for the output contract, corrupting the response.
        schema = ss.synthesize(system_text) or ss.synthesize(text)
        out = json.dumps(schema) if schema else "{}"
    else:
        hints = [w for w in ("EU-retail", "collision", "risk", "compliance",
                             "hypothesis", "ring", "migrat")
                 if w.lower() in text.lower()]
        out = ("[FAKE BACKEND] plausible narrative response"
               + (f" touching on: {', '.join(hints)}." if hints else "."))

    return fc.LLMResult(text=out, model_deployment=deployment,
                        input_tokens=max(1, len(text) // 4),
                        output_tokens=max(1, len(out) // 4))


def fake_chat_json(deployment, messages, **kw):
    kw.setdefault("response_format", {"type": "json_object"})
    res = fake_chat(deployment, messages, **kw)
    return json.loads(res.text), res


_AGENT_STEP_HINTS = {
    "p02-": ["get_account", "list_tickets", "get_contract_terms"],
    "p06-": ["file_search"],
}


def fake_run_agent(agent_id, user_message, *, timeout_s=180, on_tool_approval=None):
    hints: list[str] = []
    for prefix, tools in _AGENT_STEP_HINTS.items():
        if agent_id.startswith(prefix):
            hints = tools
            break
    approvals = []
    if on_tool_approval is not None and hints:
        approve, reason = on_tool_approval(hints[-1], '{"discount_pct": 10}')
        approvals.append({"name": hints[-1], "approve": approve, "reason": reason})

    steps = [{"type": "tool_calls", "tool_calls": [{"name": t}]} for t in hints]
    final = (f"[FAKE BACKEND] assessment referencing {', '.join(hints)}."
            if hints else "[FAKE BACKEND] plausible copilot response.")
    return {
        "final": final,
        "thread_id": f"fake-thread-{uuid.uuid4().hex[:8]}",
        "run_id": f"fake-run-{uuid.uuid4().hex[:8]}",
        "status": "completed",
        "steps": steps,
        "usage": {"prompt_tokens": 20, "completion_tokens": 20},
        "approvals": approvals,
    }


def fake_upsert_agent(name, *, deployment, instructions_path, tools=None,
                      tool_resources=None, description=""):
    return f"fake-agent-{name}"


class _ToolCollector:
    """Stands in for FastMCP's `mcp` object during route registration: the
    real route files do `@mcp.tool()` on plain functions, so `.tool()` here
    just needs to return an identity decorator that records the function."""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def tool(self, *a, **kw):
        def _decorator(fn):
            self.tools[fn.__name__] = fn
            return fn
        return _decorator


_REAL_MCP_TOOLS: dict[str, object] | None = None


def _load_real_mcp_tools() -> dict[str, object]:
    """Import the REAL MCP route handlers and collect their plain callables
    (pure Python, synthetic in-memory data) — no HTTP, no FastMCP server,
    no network. This is why the fake backend can't drift from the real MCP
    server's data: it calls the identical functions, just in-process."""
    mcp_server_dir = REPO_ROOT / "common" / "mcp_server"
    if str(mcp_server_dir) not in sys.path:
        sys.path.insert(0, str(mcp_server_dir))
    from routes import crm, graph, identity, migration, rules, telemetry_data
    collector = _ToolCollector()
    for module in (crm, graph, identity, migration, rules, telemetry_data):
        module.register(collector)
    return collector.tools


def fake_call_mcp_tool(tool: str, arguments: dict):
    global _REAL_MCP_TOOLS
    if _REAL_MCP_TOOLS is None:
        _REAL_MCP_TOOLS = _load_real_mcp_tools()
    fn = _REAL_MCP_TOOLS.get(tool)
    if fn is None:
        raise RuntimeError(f"fake MCP backend: unknown tool {tool!r} "
                           f"(known: {sorted(_REAL_MCP_TOOLS)})")
    return fn(**(arguments or {}))


def fake_shield_observations(documents: list[str], user_prompt: str = "") -> dict:
    haystack = " ".join(documents) + " " + user_prompt
    detected = any(marker in haystack for marker in INJECTION_MARKERS)
    return {"attack_detected": detected, "per_document": [], "checked": True}


class _InstantFailCredential:
    """Drop-in for azure.identity.DefaultAzureCredential during offline
    smoke testing. See _InstantFailServiceClient below for why this alone
    isn't enough to make things fast."""

    def get_token(self, *scopes, **kwargs):
        from azure.core.exceptions import ClientAuthenticationError
        raise ClientAuthenticationError(
            "fake backend: no real Azure credential available (by design)")

    def get_token_info(self, *scopes, **kwargs):
        return self.get_token(*scopes, **kwargs)


class _InstantFailServiceClient:
    """Constructor stand-in for BlobServiceClient/TableServiceClient during
    offline smoke testing.

    Found empirically: a fake credential whose get_token() raises instantly
    is NOT enough. azure-storage-blob's default retry policy wraps actual
    OPERATIONS (create_container, create_table, ...) and retries
    ClientAuthenticationError with exponential backoff — this alone took
    ~76 seconds per call in this environment, because auth failures aren't
    excluded from the default retry policy. Object CONSTRUCTION isn't
    retry-wrapped, so raising here — before any operation, any retry
    machinery, any network attempt — is both correct (construction would
    fail for the same underlying reason once an operation ran) and fast.
    """

    def __new__(cls, *args, **kwargs):
        from azure.core.exceptions import ClientAuthenticationError
        raise ClientAuthenticationError(
            "fake backend: no real Azure storage backend available (by design)")


def _install_instant_fail_credential() -> None:
    import azure.identity
    azure.identity.DefaultAzureCredential = _InstantFailCredential
    import azure.storage.blob
    azure.storage.blob.BlobServiceClient = _InstantFailServiceClient
    import azure.data.tables
    azure.data.tables.TableServiceClient = _InstantFailServiceClient


def install() -> None:
    """Patch the shared foundry_client/mcp_client/safety module objects.
    Sufficient for chat/chat_json/run_agent/upsert_agent (patterns look
    these up as fc.xxx at call time). NOT sufficient for call_mcp_tool in
    patterns that imported it by direct name before this ran — use
    install_into(pattern_module) for those (see module docstring)."""
    fc.chat = fake_chat
    fc.chat_json = fake_chat_json
    fc.run_agent = fake_run_agent
    fc.upsert_agent = fake_upsert_agent
    mcp_client.call_mcp_tool = fake_call_mcp_tool
    safety.shield_observations = fake_shield_observations
    _install_instant_fail_credential()


def install_into(pattern_module) -> None:
    """Patch a specific pattern module's own directly-imported names, for
    the ones that did `from reasoning_common.mcp_client import
    call_mcp_tool` (a name-copy, invisible to install()'s module-level patch
    if applied afterward).

    shield_check (patterns 05/09/10 import THIS by direct name, not
    shield_observations) needs no equivalent patch here: shield_check's own
    body calls shield_observations as a bare global inside safety.py, which
    Python resolves via that module's namespace at call time — the same
    namespace install() mutates. Verified empirically before relying on it.
    """
    if hasattr(pattern_module, "call_mcp_tool"):
        pattern_module.call_mcp_tool = fake_call_mcp_tool
