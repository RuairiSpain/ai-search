"""Single adapter for every Foundry / Agents / evals SDK surface the workshop touches.

VERIFIED baseline — these signatures were import-checked against the installed
packages (see requirements.txt; verified on PyPI 2026-07-23):
  azure-ai-projects==2.3.0  -> AIProjectClient.get_openai_client() ONLY
                               (the 1.x .agents/.datasets/.evaluations surfaces
                               are GONE in 2.x — do not follow old samples)
  azure-ai-agents==1.1.0    -> AgentsClient: create/update/list_agent, threads,
                               messages (incl. get_last_message_text_by_role),
                               runs (create/get/cancel/create_and_process),
                               run_steps
  openai==2.47.0            -> chat.completions + evals (graders / eval runs)
If Microsoft ships a breaking change, fix it here once; patterns import only
this module. Re-run scripts/check_package_versions.py before workshops.
"""
from __future__ import annotations

import functools
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from azure.ai.agents import AgentsClient
from azure.ai.agents.models import ListSortOrder, MessageRole
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

from .config import shared_env


# --------------------------------------------------------------------------- #
# Clients (cached: credential chain resolution is expensive)
# --------------------------------------------------------------------------- #
@functools.lru_cache(maxsize=1)
def _credential() -> DefaultAzureCredential:
    return DefaultAzureCredential()


@functools.lru_cache(maxsize=1)
def project_client() -> AIProjectClient:
    return AIProjectClient(endpoint=shared_env()["FOUNDRY_PROJECT_ENDPOINT"],
                           credential=_credential())


@functools.lru_cache(maxsize=1)
def agents_client() -> AgentsClient:
    """azure-ai-agents data plane: agents, threads, messages, runs."""
    return AgentsClient(endpoint=shared_env()["FOUNDRY_PROJECT_ENDPOINT"],
                        credential=_credential())


@functools.lru_cache(maxsize=1)
def openai_client():
    """AOAI-compatible surface for the project (chat + evals)."""
    return project_client().get_openai_client()


# --------------------------------------------------------------------------- #
# Chat completions (messages-style; used by custom control loops)
# --------------------------------------------------------------------------- #
@dataclass
class LLMResult:
    text: str
    model_deployment: str
    input_tokens: int = 0
    output_tokens: int = 0
    latency_s: float = 0.0
    raw: Any = field(default=None, repr=False)


def chat(
    deployment: str,
    messages: list[dict],
    *,
    temperature: float | None = None,
    response_format: dict | None = None,
    max_output_tokens: int | None = None,
) -> LLMResult:
    """One model call against a named *deployment* (see infra/shared/main.bicep).

    Deployment names — not model names — are the currency of this workshop:
    variants/*.yaml swaps deployments, code never changes.
    """
    client = openai_client()
    t0 = time.monotonic()
    kwargs: dict[str, Any] = {"model": deployment, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if response_format is not None:
        kwargs["response_format"] = response_format
    if max_output_tokens is not None:
        kwargs["max_completion_tokens"] = max_output_tokens
    try:
        resp = client.chat.completions.create(**kwargs)
    except Exception as e:
        # Reasoning-model deployments (gpt-5 family, o-series) often reject
        # sampling params. Retry once without them rather than failing the run.
        if "temperature" in kwargs and ("temperature" in str(e).lower()
                                        or "unsupported" in str(e).lower()):
            kwargs.pop("temperature", None)
            resp = client.chat.completions.create(**kwargs)
        else:
            raise
    usage = getattr(resp, "usage", None)
    return LLMResult(
        text=resp.choices[0].message.content or "",
        model_deployment=deployment,
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        latency_s=time.monotonic() - t0,
        raw=resp,
    )


def chat_json(deployment: str, messages: list[dict], **kw) -> tuple[dict, LLMResult]:
    """chat() but demands a JSON object back; retries once on parse failure."""
    kw.setdefault("response_format", {"type": "json_object"})
    for attempt in (1, 2):
        res = chat(deployment, messages, **kw)
        try:
            return json.loads(res.text), res
        except json.JSONDecodeError:
            if attempt == 2:
                raise
            messages = messages + [
                {"role": "assistant", "content": res.text},
                {"role": "user", "content": "That was not valid JSON. Reply with ONLY the JSON object."},
            ]
    raise RuntimeError("unreachable")


# --------------------------------------------------------------------------- #
# Agents (declarative: instructions + tools + knowledge)
# --------------------------------------------------------------------------- #
def upsert_agent(
    name: str,
    *,
    deployment: str,
    instructions_path: Path,
    tools: list[dict] | None = None,
    tool_resources: dict | None = None,
    description: str = "",
) -> str:
    """Create or update an agent from a markdown instructions file; returns id.

    Instructions live in git-versioned markdown — the Section 10 discipline:
    prompts are artefacts, not portal state. Tools are passed as a raw body
    dict because azure-ai-agents 1.1.0 has no typed model for the MCP tool
    type yet; the service accepts the JSON shape.
    """
    ac = agents_client()
    body: dict[str, Any] = {
        "model": deployment,
        "name": name,
        "description": description,
        "instructions": instructions_path.read_text(encoding="utf-8"),
        "tools": tools or [],
    }
    if tool_resources:
        body["tool_resources"] = tool_resources
    existing = next((a for a in ac.list_agents() if a.name == name), None)
    agent = ac.update_agent(existing.id, body=body) if existing else ac.create_agent(body=body)
    return agent.id


def delete_agents_by_prefix(prefix: str) -> list[str]:
    """Delete every agent whose name starts with `prefix`; returns their names.

    Teardown lives here rather than in each pattern's destroy.sh heredoc: the
    previous copy-pasted version called `project_client().agents.*`, which does
    not exist on azure-ai-projects 2.x (the client exposes only close /
    get_openai_client / send_request) and silently rotted through two SDK
    migrations because shell-embedded Python is not import-testable.
    """
    ac = agents_client()
    deleted = []
    for a in list(ac.list_agents()):
        if a.name and a.name.startswith(prefix):
            ac.delete_agent(a.id)
            deleted.append(a.name)
    return deleted


def delete_vector_stores_by_prefix(prefix: str) -> list[str]:
    """Same, for vector stores (pattern 06 semantic memory)."""
    ac = agents_client()
    deleted = []
    for v in list(ac.vector_stores.list()):
        if v.name and v.name.startswith(prefix):
            ac.vector_stores.delete(v.id)
            deleted.append(v.name)
    return deleted


def mcp_tool(server_label: str, server_url: str, allowed_tools: list[str] | None = None,
             require_approval: str | dict = "never") -> dict:
    """Tool definition attaching a (real) MCP server to an agent.

    require_approval:
      "never"  — unattended (eval runs; default). Defensible for pattern 02
                 because the only write produces a pending-approval draft.
      "always" — every tool call pauses for approval.
      dict     — granular, e.g. {"never": {"tool_names": [reads...]}} which
                 leaves unlisted tools (the write) requiring approval: the
                 steerable variants use this. §6: gate the irreversible class.
    """
    d: dict[str, Any] = {"type": "mcp", "server_label": server_label,
                         "server_url": server_url, "require_approval": require_approval}
    if allowed_tools:
        d["allowed_tools"] = allowed_tools
    return d


def knowledge_tool(index_name: str) -> tuple[list[dict], dict] | None:
    """Azure AI Search knowledge attachment: (tools, tool_resources) or None.

    Requires a project *connection* to the Search service. Set
    SEARCH_CONNECTION_ID in .shared-env / env (portal -> project -> Connected
    resources -> the connection's id) — without it we return None and the
    caller should warn rather than fail, so MCP-only flows keep working.
    """
    conn = shared_env().get("SEARCH_CONNECTION_ID", "")
    if not conn:
        return None
    from azure.ai.agents.models import AzureAISearchTool  # typed helper exists for this one
    t = AzureAISearchTool(index_connection_id=conn, index_name=index_name)
    return list(t.definitions), t.resources.as_dict() if hasattr(t.resources, "as_dict") else t.resources


def _extract_approval_requests(run) -> list[dict]:
    """Pull pending tool-approval requests out of run.required_action.

    azure-ai-agents 1.1.0 has no typed MCP-approval models (verified — only a
    generic RequiredAction), so this parses the raw dict defensively. If your
    service returns a different shape, this function and _submit_approvals are
    the only two places to adjust.
    """
    ra = getattr(run, "required_action", None)
    if ra is None:
        return []
    d = ra.as_dict() if hasattr(ra, "as_dict") else dict(ra)
    reqs = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") in ("mcp", "mcp_approval_request") or (
                    "approv" in str(node.get("type", "")).lower()):
                reqs.append(node)
            elif ("name" in node or "tool_name" in node) and ("arguments" in node or "input" in node):
                reqs.append(node)
            else:
                for v in node.values():
                    walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(d)
    out = []
    for r in reqs:
        out.append({"id": r.get("id") or r.get("tool_call_id", ""),
                    "name": r.get("name") or r.get("tool_name", "unknown"),
                    "arguments": r.get("arguments") or r.get("input", "{}")})
    return out


def _submit_approvals(ac, thread_id: str, run_id: str, decisions: list[dict]):
    """Raw-body submission (no typed model in 1.1.0): [{tool_call_id, approve}]."""
    ac.runs.submit_tool_outputs(
        thread_id=thread_id, run_id=run_id,
        body={"tool_approvals": [{"tool_call_id": d["id"], "approve": bool(d["approve"])}
                                  for d in decisions]})


def run_agent(agent_id: str, user_message: str, *, timeout_s: int = 180,
              on_tool_approval=None) -> dict:
    """Create a thread, post the message, run to completion, return transcript.

    Manual poll (not create_and_process) so the wall-clock budget can CANCEL
    the run — §18's guardrail — instead of waiting on the service.

    on_tool_approval: optional callback(name, arguments) -> (approve: bool,
    reason: str). When the run parks in requires_action with pending tool
    approvals (steerable variants), each request is routed through it; a
    rejection reason is posted to the thread so the agent can revise —
    approval gates a commit, the reason steers the trajectory. Time spent in
    the callback extends the deadline: human thinking isn't a budget cost.
    Returns {"final": str, "steps": [...], "usage": {...}, "approvals": [...]}.
    """
    ac = agents_client()
    thread = ac.threads.create()
    ac.messages.create(thread_id=thread.id, role="user", content=user_message)
    run = ac.runs.create(thread_id=thread.id, agent_id=agent_id)

    deadline = time.monotonic() + timeout_s
    approvals_log: list[dict] = []
    while run.status in ("queued", "in_progress", "requires_action"):
        if run.status == "requires_action" and on_tool_approval is None:
            # Fail fast: the agent is gated on a tool approval and no handler
            # was supplied, so polling can only end in the wall-clock guard.
            # Previously this burned the full timeout and produced an opaque
            # TimeoutError on every row of an approval-gated eval.
            pending = _extract_approval_requests(run)
            ac.runs.cancel(thread_id=thread.id, run_id=run.id)
            raise RuntimeError(
                "Run requires tool approval but no on_tool_approval handler was "
                f"supplied (pending: {[p['name'] for p in pending] or 'unknown'}). "
                "Pass a handler, or use a variant whose tools are not approval-gated.")
        if run.status == "requires_action" and on_tool_approval is not None:
            pending = _extract_approval_requests(run)
            if pending:
                decisions = []
                for req in pending:
                    t_wait = time.monotonic()
                    approve, reason = on_tool_approval(req["name"], req["arguments"])
                    deadline += time.monotonic() - t_wait  # pause clock for humans
                    decisions.append({"id": req["id"], "approve": approve})
                    approvals_log.append({**req, "approve": approve, "reason": reason})
                    if not approve and reason:
                        ac.messages.create(
                            thread_id=thread.id, role="user",
                            content=f"[REVIEWER] Rejected {req['name']}: {reason}. Revise accordingly.")
                _submit_approvals(ac, thread.id, run.id, decisions)
        if time.monotonic() > deadline:
            ac.runs.cancel(thread_id=thread.id, run_id=run.id)
            raise TimeoutError(f"run {run.id} exceeded {timeout_s}s (budget guardrail)")
        time.sleep(2)
        run = ac.runs.get(thread_id=thread.id, run_id=run.id)

    final = ac.messages.get_last_message_text_by_role(
        thread_id=thread.id, role=MessageRole.AGENT) or ""
    if hasattr(final, "text"):  # MessageTextContent vs plain str across versions
        final = final.text.value
    steps = []
    for s in ac.run_steps.list(thread_id=thread.id, run_id=run.id, order=ListSortOrder.ASCENDING):
        steps.append(s.as_dict() if hasattr(s, "as_dict") else str(s))
    usage = getattr(run, "usage", None)
    return {
        "final": str(final),
        "thread_id": thread.id,
        "run_id": run.id,
        "status": str(run.status),
        "steps": steps,
        "usage": usage.as_dict() if hasattr(usage, "as_dict") else (usage or {}),
        "approvals": approvals_log,
    }


# --------------------------------------------------------------------------- #
# Evaluations — OpenAI evals API surfaced by the Foundry project (2.x path)
# --------------------------------------------------------------------------- #
ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"},
        "query": {"type": "string"},
        "response": {"type": "string"},
        "ground_truth": {"type": "string"},
        "tool_calls": {"type": "string"},
    },
    "required": ["query", "response"],
}


def score_grader(name: str, rubric: str, deployment: str,
                 pass_threshold: float = 4.0, score_range: tuple = (1, 5)) -> dict:
    """A score_model grader: the judge reads query/response/ground_truth via
    {{ item.* }} templating and returns a numeric score. The rubric text is
    the part that carries the workshop's value — the wrapper is plumbing.

    Shape verified against openai==2.47.0 `ScoreModelGrader` (fields: type,
    name, model, input, range, sampling_params) and the eval create/run param
    TypedDicts.

    CAVEAT on `pass_threshold`: it is NOT a field on any grader type in this
    SDK version — the model has `extra="allow"`, so it is passed through
    untouched and the service may honour or ignore it. Do NOT build a release
    gate that depends on it silently working: apply thresholds when READING
    scores (that's what the pattern READMEs tell you to do), and treat this
    field as forward-compatible metadata only.
    """
    return {
        "type": "score_model",
        "name": name,
        "model": deployment,
        "range": list(score_range),
        "pass_threshold": pass_threshold,  # passthrough — see caveat above
        "input": [
            {"role": "developer",
             "content": rubric + "\nReturn ONLY the numeric score in the result."},
            {"role": "user",
             "content": ("Query:\n{{ item.query }}\n\nResponse:\n{{ item.response }}\n\n"
                         "Reference / ground truth:\n{{ item.ground_truth }}\n\n"
                         "Tool trace (may be empty):\n{{ item.tool_calls }}")},
        ],
    }


def run_cloud_evaluation(
    *,
    display_name: str,
    rows: list[dict],
    testing_criteria: list[dict],
    tags: dict[str, str] | None = None,
) -> tuple[str, str]:
    """Create an eval (graders) + submit a run with inline data; returns
    (eval_id, run_id). Results appear under the project's Evaluations view."""
    client = openai_client()
    ev = client.evals.create(
        name=display_name,
        data_source_config={"type": "custom", "item_schema": ITEM_SCHEMA,
                            "include_sample_schema": False},
        testing_criteria=testing_criteria,
        metadata=tags or {},
    )
    run = client.evals.runs.create(
        eval_id=ev.id,
        name=display_name,
        metadata=tags or {},
        data_source={
            "type": "jsonl",
            "source": {"type": "file_content",
                       "content": [{"item": {k: str(v) for k, v in r.items()}} for r in rows]},
        },
    )
    return ev.id, run.id


def get_evaluation_run(eval_id: str, run_id: str) -> Any:
    return openai_client().evals.runs.retrieve(run_id, eval_id=eval_id)
