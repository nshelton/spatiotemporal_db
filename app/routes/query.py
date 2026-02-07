import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import verify_api_key
from app.db import get_connection
from app.models import (
    BBoxQueryRequest,
    EntityOut,
    QueryResponse,
    TimeQueryRequest,
)

router = APIRouter(prefix="/v1/query", tags=["query"])


def _row_to_entity(row: dict) -> EntityOut:
    """Convert a database row to an EntityOut model."""
    payload = row.get("payload")
    if isinstance(payload, str):
        payload = json.loads(payload)

    return EntityOut(
        id=UUID(str(row["id"])),
        type=row["type"],
        t_start=row["t_start"],
        t_end=row.get("t_end"),
        lat=row.get("lat"),
        lon=row.get("lon"),
        name=row.get("name"),
        color=row.get("color"),
        render_offset=row.get("render_offset"),
        source=row.get("source"),
        external_id=row.get("external_id"),
        payload=payload,
    )


# --- Time Query ---

TIME_QUERY_SQL = """
SELECT id, type, t_start, t_end,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_Y(geom) END AS lat,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_X(geom) END AS lon,
       name, color, render_offset, source, external_id, payload
FROM entities
WHERE type = ANY($1)
  AND t_range && tstzrange($2, $3, '[]')
ORDER BY t_start {order}
LIMIT $4;
"""

TIME_QUERY_RESAMPLE_SQL = """
WITH params AS (
  SELECT $2::timestamptz AS t0, $3::timestamptz AS t1, $4::int AS n
),
bins AS (
  SELECT
    i,
    (t0 + (t1 - t0) * (i + 0.5) / n) AS t_center,
    (t0 + (t1 - t0) * (i) / n)       AS t_bin_start,
    (t0 + (t1 - t0) * (i + 1) / n)   AS t_bin_end
  FROM params, generate_series(0, (SELECT n-1 FROM params)) AS i
),
candidates AS (
  SELECT b.i, e.*
  FROM bins b
  JOIN LATERAL (
    SELECT *
    FROM entities e
    WHERE e.type = ANY($1)
      AND e.t_start >= b.t_bin_start
      AND e.t_start <  b.t_bin_end
    ORDER BY ABS(EXTRACT(EPOCH FROM (e.t_start - b.t_center))) ASC
    LIMIT 1
  ) e ON TRUE
)
SELECT id, type, t_start, t_end,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_Y(geom) END AS lat,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_X(geom) END AS lon,
       name, color, render_offset, source, external_id, payload
FROM candidates
ORDER BY t_start ASC;
"""


@router.post("/time", response_model=QueryResponse)
async def query_by_time(
    query: TimeQueryRequest,
    _api_key: str = Depends(verify_api_key),
) -> QueryResponse:
    """
    Query entities by time window.

    Returns entities whose time range overlaps with the specified window.
    Optionally supports uniform resampling for dense time series data.
    """
    async with get_connection() as conn:
        # Check if resampling is requested
        if query.resample and query.resample.method == "uniform_time":
            rows = await conn.fetch(
                TIME_QUERY_RESAMPLE_SQL,
                query.types,
                query.start,
                query.end,
                query.resample.n,
            )
        else:
            # Simple query with ordering
            order_dir = "ASC" if query.order == "t_start_asc" else "DESC"
            sql = TIME_QUERY_SQL.format(order=order_dir)
            rows = await conn.fetch(
                sql,
                query.types,
                query.start,
                query.end,
                query.limit,
            )

        entities = [_row_to_entity(dict(row)) for row in rows]

    return QueryResponse(entities=entities)


# --- BBox Query ---

BBOX_QUERY_SQL = """
SELECT id, type, t_start, t_end,
       ST_Y(geom) AS lat,
       ST_X(geom) AS lon,
       name, color, render_offset, source, external_id, payload
FROM entities
WHERE type = ANY($1)
  AND geom IS NOT NULL
  AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
  AND t_range && tstzrange($6, $7, '[]')
ORDER BY {order}
LIMIT $8;
"""

BBOX_QUERY_NO_TIME_SQL = """
SELECT id, type, t_start, t_end,
       ST_Y(geom) AS lat,
       ST_X(geom) AS lon,
       name, color, render_offset, source, external_id, payload
FROM entities
WHERE type = ANY($1)
  AND geom IS NOT NULL
  AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
ORDER BY {order}
LIMIT $6;
"""


@router.post("/bbox", response_model=QueryResponse)
async def query_by_bbox(
    query: BBoxQueryRequest,
    _api_key: str = Depends(verify_api_key),
) -> QueryResponse:
    """
    Query entities by spatial bounding box.

    Returns entities with locations within the specified bbox.
    Optionally filters by time window.

    Use order="random" for uniformly distributed random sampling.
    """
    min_lon, min_lat, max_lon, max_lat = query.bbox

    # Determine ordering
    if query.order == "random":
        order_clause = "RANDOM()"
    else:
        order_dir = "ASC" if query.order == "t_start_asc" else "DESC"
        order_clause = f"t_start {order_dir}"

    async with get_connection() as conn:
        if query.time:
            sql = BBOX_QUERY_SQL.format(order=order_clause)
            rows = await conn.fetch(
                sql,
                query.types,
                min_lon,
                min_lat,
                max_lon,
                max_lat,
                query.time.start,
                query.time.end,
                query.limit,
            )
        else:
            sql = BBOX_QUERY_NO_TIME_SQL.format(order=order_clause)
            rows = await conn.fetch(
                sql,
                query.types,
                min_lon,
                min_lat,
                max_lon,
                max_lat,
                query.limit,
            )

        entities = [_row_to_entity(dict(row)) for row in rows]

    return QueryResponse(entities=entities)
