"""gw_push_config, wired to a2a-sdk's own push-notification interfaces
(`PushNotificationConfigStore` for CRUD, the SDK's `BasePushNotificationSender`
for delivery — no bespoke delivery code needed, it already does exactly
what D3/D6 call for: POST the latest task state, `X-A2A-Notification-Token`
header if a token was registered).

IDOR is NOT re-checked here: `on_create_task_push_notification_config` /
`on_get_task_push_notification_config` / `on_list_task_push_notification_configs`
all call `task_store.get(task_id, context)` before touching this store
(verified against the installed a2a-sdk's `DefaultRequestHandlerV2`), so by
the time any method here runs, the caller is already authorised against
the task's context (D1). This store only needs to be correct about
task_id scoping.

L023 (docs/02-decisions.md D6): a push URL must resolve to a host on
`GatewayConfig.push_notification_allowlist`, checked here at write time —
the one point every registration path funnels through, regardless of
which A2A surface (JSON-RPC or REST) the client used.
"""
from __future__ import annotations

from urllib.parse import urlparse
from uuid import uuid4

import asyncpg
from a2a.server.context import ServerCallContext
from a2a.server.tasks import PushNotificationConfigStore
from a2a.types.a2a_pb2 import TaskPushNotificationConfig


class SSRFBlockedError(Exception):
    """A push URL's host isn't on the configured allowlist. Surfaces to
    the client as an internal error via the SDK's own exception handling
    — deliberately not a more specific A2A error type, so as not to leak
    the existence/shape of the allowlist to a probing client."""


def _row_to_config(row: asyncpg.Record) -> TaskPushNotificationConfig:
    cfg = TaskPushNotificationConfig(
        id=row["id"], task_id=row["task_id"], url=row["url"], token=row["token"] or ""
    )
    if row["auth_scheme"]:
        cfg.authentication.scheme = row["auth_scheme"]
        cfg.authentication.credentials = row["auth_credentials"] or ""
    return cfg


class GatewayPushConfigStore(PushNotificationConfigStore):
    def __init__(self, pool: asyncpg.Pool, *, allowlist: list[str]):
        self._pool = pool
        self._allowlist = allowlist

    async def set_info(
        self,
        task_id: str,
        notification_config: TaskPushNotificationConfig,
        context: ServerCallContext,
    ) -> None:
        host = urlparse(notification_config.url).hostname
        if not host or host not in self._allowlist:
            raise SSRFBlockedError(
                f"push-notification URL host {host!r} is not on the configured allowlist"
            )
        config_id = notification_config.id or uuid4().hex
        has_auth = notification_config.HasField("authentication")
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO gw_push_config (id, task_id, url, token, auth_scheme, auth_credentials)
                VALUES ($1, $2, $3, $4, $5, $6)
                ON CONFLICT (id) DO UPDATE SET
                    url = EXCLUDED.url,
                    token = EXCLUDED.token,
                    auth_scheme = EXCLUDED.auth_scheme,
                    auth_credentials = EXCLUDED.auth_credentials
                """,
                config_id,
                task_id,
                notification_config.url,
                notification_config.token or None,
                notification_config.authentication.scheme if has_auth else None,
                notification_config.authentication.credentials if has_auth else None,
            )
        # a2a-sdk expects the id to be readable back on the same message —
        # on_create_task_push_notification_config returns `params` as-is,
        # so a client that omitted `id` needs it filled in before that
        # return, not just persisted here.
        notification_config.id = config_id

    async def get_info(
        self, task_id: str, context: ServerCallContext
    ) -> list[TaskPushNotificationConfig]:
        return await self._fetch(task_id)

    async def get_info_for_dispatch(self, task_id: str) -> list[TaskPushNotificationConfig]:
        # No context-based partitioning to do: registrations are task-
        # scoped, not principal-scoped, and IDOR is already enforced by
        # the caller's own task_store.get() before registration — every
        # config on a task is "owned" by whoever could register it, so
        # dispatch and the user-callable read path return the same set.
        return await self._fetch(task_id)

    async def _fetch(self, task_id: str) -> list[TaskPushNotificationConfig]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM gw_push_config WHERE task_id = $1", task_id)
        return [_row_to_config(r) for r in rows]

    async def delete_info(
        self, task_id: str, context: ServerCallContext, config_id: str | None = None
    ) -> None:
        async with self._pool.acquire() as conn:
            if config_id:
                await conn.execute(
                    "DELETE FROM gw_push_config WHERE task_id = $1 AND id = $2", task_id, config_id
                )
            else:
                await conn.execute("DELETE FROM gw_push_config WHERE task_id = $1", task_id)
