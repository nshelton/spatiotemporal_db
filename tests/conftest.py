import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import app


# --- Async event loop fixture ---


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# --- Integration Test Fixtures (Local PostgreSQL) ---


@pytest.fixture(scope="session")
async def db_pool() -> AsyncGenerator[asyncpg.Pool, None]:
    """Create a database pool connected to local PostgreSQL.
    
    Requires a local PostgreSQL with PostGIS. Create test database with:
        createdb test_daruma
        psql test_daruma -f migrations/001_initial.sql
    
    Or set TEST_DATABASE_URL environment variable.
    """
    import os
    
    # Use TEST_DATABASE_URL if set, otherwise default to local
    url = os.environ.get("TEST_DATABASE_URL", "postgresql://localhost/test_daruma")
    
    # Create pool
    pool = await asyncpg.create_pool(url, min_size=1, max_size=5)

    # Run schema (safe to run multiple times due to IF NOT EXISTS)
    schema_path = Path(__file__).parent.parent / "schema.sql"
    async with pool.acquire() as conn:
        await conn.execute(schema_path.read_text())

    yield pool

    await pool.close()


@pytest.fixture
async def clean_db(db_pool: asyncpg.Pool):
    """Clean the entities table before each test."""
    async with db_pool.acquire() as conn:
        await conn.execute("TRUNCATE entities CASCADE;")
    yield


@pytest.fixture
async def integration_client(
    db_pool: asyncpg.Pool, clean_db
) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client with real database."""
    import app.db as db_module

    # Override the database pool
    original_pool = db_module._pool
    db_module._pool = db_pool

    # Override API key for testing
    original_api_key = settings.api_key
    settings.api_key = "test-api-key"

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as client:
        yield client

    # Restore originals
    db_module._pool = original_pool
    settings.api_key = original_api_key


# --- Unit Test Fixtures (Mocked) ---


@pytest.fixture
def mock_pool():
    """Create a mock database pool for unit tests."""
    pool = MagicMock()
    pool.acquire = MagicMock()

    # Create a mock connection
    mock_conn = AsyncMock()
    mock_conn.fetchrow = AsyncMock()
    mock_conn.fetch = AsyncMock()
    mock_conn.execute = AsyncMock()

    # Make acquire work as async context manager
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

    return pool, mock_conn


@pytest.fixture
async def unit_client(mock_pool) -> AsyncGenerator[AsyncClient, None]:
    """Create an async HTTP client with mocked database."""
    import app.db as db_module

    pool, mock_conn = mock_pool

    # Override the database pool
    original_pool = db_module._pool
    db_module._pool = pool

    # Override API key for testing
    original_api_key = settings.api_key
    settings.api_key = "test-api-key"

    transport = ASGITransport(app=app)
    async with AsyncClient(
        transport=transport,
        base_url="http://test",
        headers={"X-API-Key": "test-api-key"},
    ) as client:
        yield client

    # Restore originals
    db_module._pool = original_pool
    settings.api_key = original_api_key


# --- Test Data Helpers ---


def make_entity_data(
    entity_type: str = "location.gps",
    t_start: datetime | None = None,
    t_end: datetime | None = None,
    lat: float | None = 34.0522,
    lon: float | None = -118.2437,
    source: str | None = None,
    external_id: str | None = None,
    **kwargs,
) -> dict:
    """Create entity test data."""
    if t_start is None:
        t_start = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    data = {
        "type": entity_type,
        "t_start": t_start.isoformat(),
        "lat": lat,
        "lon": lon,
    }

    if t_end:
        data["t_end"] = t_end.isoformat()
    if source:
        data["source"] = source
    if external_id:
        data["external_id"] = external_id

    data.update(kwargs)
    return data
