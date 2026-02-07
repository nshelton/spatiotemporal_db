import json
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import verify_api_key
from app.db import get_connection
from app.models import EntityIn, EntityResponse

router = APIRouter(prefix="/v1", tags=["entity"])

# SQL for upsert with ON CONFLICT
UPSERT_SQL = """
INSERT INTO entities (type, t_start, t_end, lat, lon, name, color, render_offset, source, external_id, payload)
VALUES (
  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
)
ON CONFLICT (source, external_id)
WHERE source IS NOT NULL AND external_id IS NOT NULL
DO UPDATE SET
  type = EXCLUDED.type,
  t_start = EXCLUDED.t_start,
  t_end = EXCLUDED.t_end,
  lat = EXCLUDED.lat,
  lon = EXCLUDED.lon,
  name = EXCLUDED.name,
  color = EXCLUDED.color,
  render_offset = EXCLUDED.render_offset,
  payload = EXCLUDED.payload,
  updated_at = now()
RETURNING id, (xmax = 0) AS inserted;
"""

# SQL for simple insert (when no source/external_id)
INSERT_SQL = """
INSERT INTO entities (type, t_start, t_end, lat, lon, name, color, render_offset, source, external_id, payload)
VALUES (
  $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
)
RETURNING id;
"""


@router.post("/entity", response_model=EntityResponse)
async def create_entity(
    entity: EntityIn,
    _api_key: str = Depends(verify_api_key),
) -> EntityResponse:
    """
    Create or update an entity.

    If source and external_id are provided and a matching entity exists,
    the existing entity will be updated (upsert behavior).
    """
    payload_json = json.dumps(entity.payload) if entity.payload else None

    async with get_connection() as conn:
        if entity.source is not None and entity.external_id is not None:
            # Use upsert logic
            row = await conn.fetchrow(
                UPSERT_SQL,
                entity.type,
                entity.t_start,
                entity.t_end,
                entity.lat,
                entity.lon,
                entity.name,
                entity.color,
                entity.render_offset,
                entity.source,
                entity.external_id,
                payload_json,
            )
            entity_id = row["id"]
            was_inserted = row["inserted"]
            status = "inserted" if was_inserted else "updated"
        else:
            # Simple insert
            row = await conn.fetchrow(
                INSERT_SQL,
                entity.type,
                entity.t_start,
                entity.t_end,
                entity.lat,
                entity.lon,
                entity.name,
                entity.color,
                entity.render_offset,
                entity.source,
                entity.external_id,
                payload_json,
            )
            entity_id = row["id"]
            status = "inserted"

    return EntityResponse(id=UUID(str(entity_id)), status=status)
