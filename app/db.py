from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg

from app.config import settings

# Global connection pool
_pool: asyncpg.Pool | None = None


async def init_pool() -> asyncpg.Pool:
    """Initialize the database connection pool."""
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
        )
    return _pool


async def close_pool() -> None:
    """Close the database connection pool."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    """Get the current connection pool. Raises if not initialized."""
    if _pool is None:
        raise RuntimeError("Database pool not initialized. Call init_pool() first.")
    return _pool


@asynccontextmanager
async def get_connection() -> AsyncGenerator[asyncpg.Connection, None]:
    """Get a connection from the pool."""
    pool = get_pool()
    async with pool.acquire() as conn:
        yield conn
