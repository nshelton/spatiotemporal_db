Personal Timeline API (v0) — FastAPI + PostgreSQL + PostGIS
Goals

Store millions of entities with optional time span + optional location.

Support fast queries by:

time window (timeline)

spatial extent (map bbox)

type

optional downsampling to a fixed number of returned points

Keep schema simple and stable. You will keep raw source dumps separately and can re-ingest after schema changes.

Non-goals (v0)

Multi-user auth, sharing

Offline sync protocol (can add later)

Full-text search

Attachments/media storage (store metadata + URLs only)

Core Concept: Entity

Everything is an “entity” with optional fields.

Required

type: string (e.g. "location.gps", "event", "photo", "calendar.event")

t_start: timestamp (UTC)

Optional

t_end: timestamp (UTC) for spans; null = instantaneous

lat, lon (WGS84) optional

name, color, render_offset optional

payload: JSON for type-specific data (small/medium)

source, external_id for idempotent re-ingest/upserts

Database (PostgreSQL + PostGIS)
Extensions
CREATE EXTENSION IF NOT EXISTS postgis;

Table

Use:

geom for spatial (Point, 4326)

t_range as a generated range for fast overlap queries

CREATE TABLE IF NOT EXISTS entities (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  type         text NOT NULL,

  t_start      timestamptz NOT NULL,
  t_end        timestamptz NULL,

  -- generated range; inclusive bounds
  t_range      tstzrange GENERATED ALWAYS AS (
                 tstzrange(t_start, COALESCE(t_end, t_start), '[]')
               ) STORED,

  -- optional location
  geom         geometry(Point, 4326) NULL,

  name         text NULL,
  color        text NULL,          -- e.g. "#RRGGBB"
  render_offset real NULL,

  source       text NULL,          -- e.g. "import.google_calendar"
  external_id  text NULL,          -- stable id from source

  payload      jsonb NULL,

  created_at   timestamptz NOT NULL DEFAULT now(),
  updated_at   timestamptz NOT NULL DEFAULT now()
);

-- sanity constraint for spans
ALTER TABLE entities
  ADD CONSTRAINT entities_t_end_gte_start
  CHECK (t_end IS NULL OR t_end >= t_start);

-- idempotency: if you provide (source, external_id), allow upsert behavior
CREATE UNIQUE INDEX IF NOT EXISTS entities_source_external_id_uq
  ON entities(source, external_id)
  WHERE source IS NOT NULL AND external_id IS NOT NULL;

Indexes (fast time + space + type)
-- Time overlap queries: fast with GiST over range
CREATE INDEX IF NOT EXISTS entities_t_range_gist
  ON entities USING gist (t_range);

-- Filter by type (and often by time sorting)
CREATE INDEX IF NOT EXISTS entities_type_tstart_btree
  ON entities(type, t_start);

-- Spatial bbox queries
CREATE INDEX IF NOT EXISTS entities_geom_gist
  ON entities USING gist (geom)
  WHERE geom IS NOT NULL;


Notes

This is already good for millions of rows.

If GPS grows huge and append-only, you can later add monthly partitioning or TimescaleDB. Not needed for v0.

API (FastAPI)
Auth (simple, single-user)

Use a single API key header:

X-API-Key: <secret>
Reject requests without it.

Data Types (JSON)
Entity JSON (API shape)

API accepts lat/lon and server writes geom.

API returns lat/lon for convenience.

{
  "id": "uuid-optional-on-insert",
  "type": "location.gps",
  "t_start": "2024-01-01T00:00:00Z",
  "t_end": null,

  "lat": 34.0522,
  "lon": -118.2437,

  "name": "optional",
  "color": "#FF00FF",
  "render_offset": 0.0,

  "source": "import.gps_logger",
  "external_id": "deviceA:1704067200",

  "payload": { "accuracy_m": 12.3 }
}

Endpoints (v0)
1) Insert single (with optional upsert)

POST /v1/entity

Behavior:

If (source, external_id) provided and already exists, update existing row (idempotent ingest).

Else insert new.

If lat/lon provided, set geom = ST_SetSRID(ST_MakePoint(lon, lat), 4326).

Request: Entity JSON (above)
Response: { "id": "...", "status": "inserted" | "updated" }

Upsert SQL sketch

INSERT INTO entities (type, t_start, t_end, geom, name, color, render_offset, source, external_id, payload)
VALUES (
  $1, $2, $3,
  CASE WHEN $4 IS NULL OR $5 IS NULL THEN NULL
       ELSE ST_SetSRID(ST_MakePoint($5, $4), 4326) END,
  $6, $7, $8, $9, $10, $11
)
ON CONFLICT (source, external_id)
DO UPDATE SET
  type = EXCLUDED.type,
  t_start = EXCLUDED.t_start,
  t_end = EXCLUDED.t_end,
  geom = EXCLUDED.geom,
  name = EXCLUDED.name,
  color = EXCLUDED.color,
  render_offset = EXCLUDED.render_offset,
  payload = EXCLUDED.payload,
  updated_at = now()
RETURNING id;

2) Query by time window (timeline)

POST /v1/query/time

Request:

{
  "types": ["location.gps", "event"],
  "start": "2020-01-01T00:00:00Z",
  "end":   "2021-01-01T00:00:00Z",

  "limit": 2000,
  "order": "t_start_asc",

  "resample": { "method": "none" }
}


Response:

{ "entities": [ ... ] }


Time overlap rule
Return entities whose time overlaps [start,end]:

t_range && tstzrange(start, end, '[]')

SQL sketch:

SELECT id, type, t_start, t_end,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_Y(geom) END AS lat,
       CASE WHEN geom IS NULL THEN NULL ELSE ST_X(geom) END AS lon,
       name, color, render_offset, source, external_id, payload
FROM entities
WHERE type = ANY($1)
  AND t_range && tstzrange($2, $3, '[]')
ORDER BY t_start ASC
LIMIT $4;

Optional resample: uniform over time (your “getLocations(timespan, number)”)

If resample.method = "uniform_time" and n = N:

Divide the window into N bins

For each bin, return the entity nearest the bin center (for a given type set)

(v0) This is primarily for dense point series like GPS.

Request:

{
  "types": ["location.gps"],
  "start": "2020-01-01T00:00:00Z",
  "end":   "2021-01-01T00:00:00Z",
  "resample": { "method": "uniform_time", "n": 2000 }
}


SQL sketch (simple + effective):

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

3) Query by spatial extent (map bbox)

POST /v1/query/bbox

Request:

{
  "types": ["location.gps", "photo"],
  "bbox": [-118.55, 33.90, -118.15, 34.10],   // [minLon, minLat, maxLon, maxLat]

  "time": { "start": "2020-01-01T00:00:00Z", "end": "2021-01-01T00:00:00Z" },

  "limit": 5000,
  "order": "t_start_desc"
}


SQL sketch:

SELECT id, type, t_start, t_end,
       ST_Y(geom) AS lat,
       ST_X(geom) AS lon,
       name, color, render_offset, source, external_id, payload
FROM entities
WHERE type = ANY($1)
  AND geom IS NOT NULL
  AND geom && ST_MakeEnvelope($2, $3, $4, $5, 4326)
  AND t_range && tstzrange($6, $7, '[]')
ORDER BY t_start DESC
LIMIT $8;


Note: bbox queries should always require limit. (No “0 means all” in v0.)

Implementation Notes (FastAPI)

Use async and a pooled driver:

asyncpg or psycopg (psycopg3) with pool

Keep handlers thin: validate → run SQL → return JSON.

Always return only fields you need (don’t bloat payloads).

Store timestamps as timestamptz and use UTC in API.

Pydantic models (shape)

EntityIn with optional id, optional t_end, optional lat/lon, optional metadata/payload.

EntityOut always includes id.

TimeQuery, BBoxQuery request models