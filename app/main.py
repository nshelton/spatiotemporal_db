import time
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import close_pool, init_pool
from app.routes import entity, query

# Track server start time for uptime
_start_time = time.time()


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

# Include routers
app.include_router(entity.router)
app.include_router(query.router)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok"}


@app.get("/stats")
async def stats():
    """
    Get database and API statistics.

    Returns entity counts by type, time coverage, database size, and uptime.
    """
    from app.db import get_connection
    from app.models import DatabaseStats, EntityTypeStats, StatsResponse, TimeRange

    async with get_connection() as conn:
        # Get total entity count and counts by type
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

        # Get time range covered by entities
        time_range = await conn.fetchrow(
            """
            SELECT
                MIN(t_start) as oldest,
                MAX(COALESCE(t_end, t_start)) as newest
            FROM entities
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

    return StatsResponse(
        total_entities=total_result or 0,
        entities_by_type=entities_by_type,
        time_coverage=TimeRange(
            oldest=time_range["oldest"],
            newest=time_range["newest"]
        ),
        database=DatabaseStats(
            size_mb=round(db_stats["size_mb"], 2),
            table_size_mb=round(db_stats["table_size_mb"], 2),
            index_size_mb=round(db_stats["index_size_mb"], 2)
        ),
        uptime_seconds=round(time.time() - _start_time, 1)
    )


if __name__ == "__main__":
    import uvicorn

    from app.config import settings

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=True,
    )
