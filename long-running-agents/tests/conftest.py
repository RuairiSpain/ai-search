from __future__ import annotations

import os

import asyncpg
import pytest
import pytest_asyncio


@pytest_asyncio.fixture()
async def pg_pool():
    """Connects to the local docker-compose Postgres (`make db-up &&
    make migrate` first). Tests using this fixture are integration tests,
    not unit tests — see docs/05-tier2-hosted-agents.md "Run against a
    deployed [store]" for why isolation logic specifically needs a real
    database rather than a mock."""
    try:
        pool = await asyncpg.create_pool(
            host=os.environ.get("PGHOST", "localhost"),
            port=int(os.environ.get("PGPORT", "5432")),
            database=os.environ.get("PGDATABASE", "gateway"),
            user=os.environ.get("PGUSER", "gateway"),
            password=os.environ.get("PGPASSWORD", "devpassword"),
            min_size=1,
            max_size=4,
        )
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover
        pytest.skip(f"no local Postgres available: {exc}")

    yield pool
    await pool.close()
