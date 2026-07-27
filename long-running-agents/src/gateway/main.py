"""Gateway entrypoint. `uvicorn gateway.main:app` or `make run`.

Startup wires the DB pool, loads apps.yaml, builds the adapter registry,
and probes every upstream's health() — including T2's delegation probe,
which is a startup-time check on purpose (docs/05-tier2-hosted-agents.md
§3.4 "Probe delegation at startup, not at first real user request").
A missing UserIdentityImpersonation grant fails readiness rather than
surfacing as a 403 to the first real user.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException

from gateway.a2a_server.app import mount_app
from gateway.a2a_server.push_config import GatewayPushConfigStore
from gateway.api.webhooks import build_webhook_router
from gateway.config import get_config
from gateway.registry import Registry
from gateway.store.artifact_store import ArtifactStore
from gateway.store.context_store import ContextStore
from gateway.store.db import Database
from gateway.store.interjection_store import InterjectionStore
from gateway.store.message_store import MessageStore
from gateway.store.task_store import TaskStore

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "info").upper())
log = logging.getLogger("gateway")


async def _run_reaper(tasks: TaskStore, *, interval_s: float, lease_grace_s: int) -> None:
    """Periodically fails tasks whose lease has lapsed (docs/03
    "gw_task_reaper", docs/08). Runs for the life of the process; the
    lifespan cancels it on shutdown. A failure in one sweep must not kill
    the loop — the next sweep is still worth attempting."""
    while True:
        try:
            await asyncio.sleep(interval_s)
            reaped = await tasks.reap_wedged_tasks(lease_grace_s=lease_grace_s)
            if reaped:
                log.warning("reaper failed %d wedged task(s): %s", len(reaped), reaped)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("reaper sweep failed; will retry next interval")


@asynccontextmanager
async def lifespan(app: FastAPI):
    config = get_config()
    db = await Database.connect()

    contexts = ContextStore(db.pool)
    tasks = TaskStore(db.pool)
    artifacts = ArtifactStore(db.pool)
    messages = MessageStore(db.pool)
    interjections = InterjectionStore(db.pool)
    push_config_store = GatewayPushConfigStore(
        db.pool, allowlist=config.push_notification_allowlist
    )
    push_http_client = httpx.AsyncClient(timeout=10.0)
    registry = Registry(config, tasks, artifacts)
    registry.build()

    health = await registry.health_check_all()
    unhealthy = [uid for uid, ok in health.items() if not ok]
    if unhealthy:
        # Readiness, not liveness: log loudly but don't crash the process
        # on a transient upstream blip. `/healthz` reflects this state so
        # an orchestrator can hold traffic until it clears.
        log.error("upstreams failing health check at startup: %s", unhealthy)

    app.state.config = config
    app.state.db = db
    app.state.contexts = contexts
    app.state.tasks = tasks
    app.state.registry = registry
    app.state.last_health = health

    # One A2A-conformant surface per configured app (T2/T3 only — docs/00
    # §4). Each app gets its own AgentExecutor/TaskStore/AgentCard, all
    # sharing the one Postgres-backed store layer above.
    request_handlers = [
        mount_app(
            app,
            app_cfg=app_cfg,
            adapter=registry.adapter_for_app(app_cfg.name),
            validator=registry.validator,
            contexts=contexts,
            tasks=tasks,
            artifacts=artifacts,
            messages=messages,
            harvester=registry.harvester,
            interjections=interjections,
            push_config_store=push_config_store,
            push_http_client=push_http_client,
        )
        for app_cfg in config.apps
    ]
    app.include_router(build_webhook_router(tasks), prefix="/callback")

    reaper_task = asyncio.create_task(
        _run_reaper(
            tasks,
            interval_s=config.reaper_interval_seconds,
            lease_grace_s=config.reaper_lease_grace_seconds,
        )
    )

    try:
        yield
    finally:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
        for handler in request_handlers:
            await handler.aclose()
        await push_http_client.aclose()
        await db.close()


app = FastAPI(title="A2A Gateway", lifespan=lifespan)


@app.get("/healthz")
async def healthz():
    health = getattr(app.state, "last_health", {})
    if health and not all(health.values()):
        raise HTTPException(503, {"upstreams": health})
    return {"upstreams": health}
