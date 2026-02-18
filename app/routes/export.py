import json
from typing import AsyncGenerator, Literal

import orjson
from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.auth import verify_api_key
from app.db import get_connection

router = APIRouter(prefix="/v1/query", tags=["query"])

EXPORT_COUNT_SQL = """
SELECT COUNT(*) FROM entities
WHERE type = ANY($1);
"""

EXPORT_COUNT_ALL_SQL = """
SELECT COUNT(*) FROM entities;
"""

EXPORT_STREAM_SQL = """
SELECT id, type, t_start, t_end,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_Y(geom) END AS lat,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_X(geom) END AS lon,
       name, color, render_offset, source, external_id, loc_source, payload
FROM entities
{where}
ORDER BY t_start {order};
"""

CURSOR_BATCH_SIZE = 5000


def _row_to_dict(row) -> dict:
    """Convert a database row to a plain dict, fast path avoiding Pydantic."""
    payload = row["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    return {
        "id": str(row["id"]),
        "type": row["type"],
        "t_start": row["t_start"].isoformat(),
        "t_end": row["t_end"].isoformat() if row["t_end"] else None,
        "lat": row["lat"],
        "lon": row["lon"],
        "name": row["name"],
        "color": row["color"],
        "render_offset": row["render_offset"],
        "source": row["source"],
        "external_id": row["external_id"],
        "loc_source": row["loc_source"],
        "payload": payload,
    }


async def _stream_entities(
    types: list[str] | None,
    order: str = "DESC",
) -> AsyncGenerator[bytes, None]:
    """Stream all entities as NDJSON: first line is metadata, rest are entities."""
    async with get_connection() as conn:
        # Get total count
        if types:
            total = await conn.fetchval(EXPORT_COUNT_SQL, types)
            where = "WHERE type = ANY($1)"
            sql = EXPORT_STREAM_SQL.format(where=where, order=order)
            args = [types]
        else:
            total = await conn.fetchval(EXPORT_COUNT_ALL_SQL)
            sql = EXPORT_STREAM_SQL.format(where="", order=order)
            args = []

        # First line: metadata
        yield orjson.dumps({"total": total}) + b"\n"

        # Stream rows using a cursor (fetches in batches from PG)
        async with conn.transaction():
            cursor = conn.cursor(sql, *args)
            async for row in cursor:
                yield orjson.dumps(_row_to_dict(row)) + b"\n"


@router.get("/export")
async def export_entities(
    _api_key: str = Depends(verify_api_key),
    types: list[str] | None = Query(None, description="Entity types to export. Omit for all types."),
    order: Literal["newest", "oldest"] = Query("newest", description="Sort order: 'newest' (default) or 'oldest' first."),
):
    """
    Stream all entities as newline-delimited JSON (NDJSON).

    The first line is a metadata object with the total count:
        {"total": 4000000}

    Each subsequent line is one entity as JSON:
        {"id": "...", "type": "...", ...}

    Use gzip Accept-Encoding for ~70-80% size reduction.
    Optionally filter by entity type(s) via the `types` query parameter.
    """
    order_dir = "DESC" if order == "newest" else "ASC"
    return StreamingResponse(
        _stream_entities(types, order=order_dir),
        media_type="application/x-ndjson",
        headers={
            "X-Content-Type-Options": "nosniff",
        },
    )
