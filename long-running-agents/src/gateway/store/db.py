"""Postgres connection pool.

docs/03-postgres-schema.md "Azure Postgres with Entra auth": tokens expire
hourly, so the pool needs a token-refreshing connect path in Azure. Local
dev (docker-compose) uses plain password auth instead — see
PG_USE_ENTRA_AUTH in .env.example.
"""
from __future__ import annotations

import os

import asyncpg
from azure.identity.aio import DefaultAzureCredential

_ENTRA_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


class Database:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    @property
    def pool(self) -> asyncpg.Pool:
        return self._pool

    @classmethod
    async def connect(cls) -> Database:
        use_entra = os.environ.get("PG_USE_ENTRA_AUTH", "false").lower() == "true"
        host = os.environ["PGHOST"]
        port = int(os.environ.get("PGPORT", "5432"))
        database = os.environ["PGDATABASE"]
        user = os.environ["PGUSER"]

        if use_entra:
            credential = DefaultAzureCredential()

            async def _password() -> str:
                # Re-acquired on every new physical connection asyncpg
                # opens, so pool connections created after the first
                # token expiry still succeed.
                token = await credential.get_token(_ENTRA_SCOPE)
                return token.token

            pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=_password,
                ssl="require",
                min_size=2,
                max_size=10,
            )
        else:
            pool = await asyncpg.create_pool(
                host=host,
                port=port,
                database=database,
                user=user,
                password=os.environ.get("PGPASSWORD"),
                min_size=2,
                max_size=10,
            )
        return cls(pool)

    async def close(self) -> None:
        await self._pool.close()
