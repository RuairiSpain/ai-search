"""Mounts one A2A-conformant surface per configured app onto the shared
FastAPI instance, at the same `/apps/{app}/...` layout the old hand-rolled
router used — JSON-RPC at `/apps/{app}/`, REST at `/apps/{app}/...`, and
the agent card at `/apps/{app}/.well-known/agent-card.json`.
"""
from __future__ import annotations

from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from fastapi import FastAPI

from gateway.a2a_server.card import build_agent_card
from gateway.a2a_server.context import GatewayCallContextBuilder
from gateway.a2a_server.executor import GatewayAgentExecutor
from gateway.a2a_server.task_store import GatewayTaskStoreAdapter
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import AppConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.task_store import TaskStore
from gateway.upstream.base import UpstreamAdapter


def mount_app(
    fastapi_app: FastAPI,
    *,
    app_cfg: AppConfig,
    adapter: UpstreamAdapter,
    validator: EntraValidator,
    contexts: ContextStore,
    tasks: TaskStore,
    artifacts: ArtifactStore,
    harvester: ArtifactHarvester,
) -> DefaultRequestHandler:
    """Returns the request handler so the caller can `await handler.aclose()`
    on shutdown — it drains in-flight executions cleanly (per the SDK's own
    docstring on DefaultRequestHandlerV2.aclose)."""
    executor = GatewayAgentExecutor(
        app=app_cfg.name,
        tier=app_cfg.tier,
        adapter=adapter,
        contexts=contexts,
        tasks=tasks,
        harvester=harvester,
        # T2/T3 are long-running by nature (docs/00 §4) — always
        # non-blocking upstream calls; the SDK's own return_immediately
        # governs whether the HTTP response waits for a terminal state.
        default_blocking=False,
        budget_ms=app_cfg.sync_budget_ms,
    )
    task_store = GatewayTaskStoreAdapter(
        gw_tasks=tasks, gw_contexts=contexts, gw_artifacts=artifacts, harvester=harvester
    )
    agent_card = build_agent_card(app_cfg, adapter.capabilities)
    context_builder = GatewayCallContextBuilder(validator)

    prefix = f"/apps/{app_cfg.name}"
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
    )

    add_a2a_routes_to_fastapi(
        fastapi_app,
        agent_card_routes=create_agent_card_routes(
            agent_card, card_url=f"{prefix}/.well-known/agent-card.json"
        ),
        jsonrpc_routes=create_jsonrpc_routes(
            request_handler, rpc_url=f"{prefix}/", context_builder=context_builder
        ),
        rest_routes=create_rest_routes(
            request_handler, context_builder=context_builder, path_prefix=prefix
        ),
    )
    return request_handler
