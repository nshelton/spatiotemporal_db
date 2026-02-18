import asyncio
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.middleware.gzip import GZipMiddleware

from app.db import close_pool, init_pool
from app.routes import entity, export, query

# Track server start time for uptime
_start_time = time.time()

# Stats cache (5-minute TTL)
_stats_cache = None
_stats_cache_time = 0.0
STATS_CACHE_TTL = 300  # 5 minutes


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: initialize and cleanup resources."""
    # Startup
    await init_pool()
    yield
    # Shutdown
    await close_pool()


app = FastAPI(
    title="Daruma - Personal Timeline API",
    description="Store and query entities with time spans and locations",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(GZipMiddleware, minimum_size=1000)

# Include routers
app.include_router(entity.router)
app.include_router(query.router)
app.include_router(export.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    """
    Get database and API statistics.

    Returns entity counts by type, time coverage, database size, and uptime.
    Cached for 5 minutes for performance.
    """
    global _stats_cache, _stats_cache_time

    # Check cache first
    current_time = time.time()
    if _stats_cache is not None and (current_time - _stats_cache_time) < STATS_CACHE_TTL:
        # Update uptime in cached response
        cached_response = _stats_cache.copy()
        cached_response["uptime_seconds"] = round(current_time - _start_time, 1)
        return cached_response

    from app.db import get_connection
    from app.models import DatabaseStats, EntityTypeStats, StatsResponse, TimeRange

    async with get_connection() as conn:
        # Run queries sequentially (asyncpg connections cannot handle concurrent operations)
        # Get entity counts by type
        type_counts = await conn.fetch(
            """
            SELECT type, COUNT(*) as count
            FROM entities
            GROUP BY type
            ORDER BY count DESC
            """
        )

        # Get total count
        total_result = await conn.fetchval("SELECT COUNT(*) FROM entities")

        # Get oldest timestamp (uses index efficiently)
        oldest = await conn.fetchval("SELECT MIN(t_start) FROM entities")

        # Get newest timestamp (optimized to use indexes)
        newest = await conn.fetchval(
            """
            SELECT GREATEST(
                COALESCE((SELECT MAX(t_end) FROM entities WHERE t_end IS NOT NULL), '1970-01-01'::timestamptz),
                COALESCE((SELECT MAX(t_start) FROM entities), '1970-01-01'::timestamptz)
            )
            """
        )

        # Get database size statistics
        db_stats = await conn.fetchrow(
            """
            SELECT
                pg_database_size(current_database()) / (1024.0 * 1024.0) as size_mb,
                pg_total_relation_size('entities') / (1024.0 * 1024.0) as table_size_mb,
                pg_indexes_size('entities') / (1024.0 * 1024.0) as index_size_mb
            """
        )

    # Build response
    entities_by_type = [
        EntityTypeStats(type=row["type"], count=row["count"])
        for row in type_counts
    ]

    response = StatsResponse(
        total_entities=total_result or 0,
        entities_by_type=entities_by_type,
        time_coverage=TimeRange(
            oldest=oldest,
            newest=newest
        ),
        database=DatabaseStats(
            size_mb=round(db_stats["size_mb"], 2),
            table_size_mb=round(db_stats["table_size_mb"], 2),
            index_size_mb=round(db_stats["index_size_mb"], 2)
        ),
        uptime_seconds=round(current_time - _start_time, 1)
    )

    # Cache the response (convert to dict for copying)
    _stats_cache = response.model_dump()
    _stats_cache_time = current_time

    return response


if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
