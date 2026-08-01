"""Mounts one A2A-conformant surface per configured app onto the shared
FastAPI instance, at the same `/apps/{app}/...` layout the old hand-rolled
router used — JSON-RPC at `/apps/{app}/`, REST at `/apps/{app}/...`, and
the agent card at `/apps/{app}/.well-known/agent-card.json`.
"""
from __future__ import annotations

import httpx
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes import (
    add_a2a_routes_to_fastapi,
    create_agent_card_routes,
    create_jsonrpc_routes,
    create_rest_routes,
)
from a2a.server.tasks import BasePushNotificationSender
from fastapi import FastAPI

from gateway.a2a_server.card import build_agent_card
from gateway.a2a_server.context import GatewayCallContextBuilder
from gateway.a2a_server.executor import GatewayAgentExecutor
from gateway.a2a_server.interjections import build_interjection_router
from gateway.a2a_server.push_config import GatewayPushConfigStore
from gateway.a2a_server.task_store import GatewayTaskStoreAdapter
from gateway.artifacts import ArtifactHarvester
from gateway.auth.principal import EntraValidator
from gateway.config import AppConfig
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.interjection_store import InterjectionStore
from gateway.store.message_store import MessageStore
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
    messages: MessageStore,
    harvester: ArtifactHarvester,
    interjections: InterjectionStore,
    push_config_store: GatewayPushConfigStore,
    push_http_client: httpx.AsyncClient,
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
        lease_seconds=app_cfg.lease_seconds,
    )
    task_store = GatewayTaskStoreAdapter(
        gw_tasks=tasks,
        gw_contexts=contexts,
        gw_artifacts=artifacts,
        gw_messages=messages,
        harvester=harvester,
    )
    agent_card = build_agent_card(app_cfg, adapter.capabilities)
    context_builder = GatewayCallContextBuilder(validator)

    prefix = f"/apps/{app_cfg.name}"
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=task_store,
        agent_card=agent_card,
        # Present regardless of whether this app's card advertises
        # pushNotifications=true — the SDK's own capability check on the
        # card (`@validate(lambda self: self._agent_card.capabilities.push_notifications, ...)`)
        # is what actually gates whether a client can register one, not
        # whether the store exists.
        push_config_store=push_config_store,
        push_sender=BasePushNotificationSender(push_http_client, push_config_store),
    )

    # Gateway-owned extension, not part of the A2A route set below — see
    # a2a_server/interjections.py for why steering can't live on the
    # standard message/send surface. Registered BEFORE
    # add_a2a_routes_to_fastapi() and appended directly to
    # fastapi_app.routes (mirroring the SDK's own `_attach_route`, which
    # does the same raw append) — both matter, for the same underlying
    # reason: `create_rest_routes()` always includes a
    # `Mount(path='/{tenant}', ...)` (an undocumented multi-tenancy catch-
    # all, present regardless of whether tenancy is used) whose path regex
    # `^/(?P<tenant>[^/]+)/(?P<path>.*)$` matches almost any 2+-segment
    # path. Starlette tries routes in registration order and a matching
    # Mount fully delegates rather than falling through, so any route
    # registered AFTER that Mount is unreachable if its path also fits the
    # pattern -- reproduced in isolation (a bare 404 on every interject
    # call) before finding the cause here. Registering first means our
    # exact route wins the match before the catch-all Mount is even tried.
    interjection_router = build_interjection_router(
        prefix=prefix,
        app_cfg_name=app_cfg.name,
        adapter=adapter,
        validator=validator,
        contexts=contexts,
        tasks=tasks,
        interjections=interjections,
    )
    fastapi_app.routes.extend(interjection_router.routes)

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
