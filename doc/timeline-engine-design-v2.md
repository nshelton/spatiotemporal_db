# Timeline Engine — Design Document (v2)

A plugin-based framework for ingesting arbitrary personal data sources into a unified PostgreSQL spatiotemporal database. Drop a single Python file into `sources/` to add a new data type — no schema migrations, no config changes, no infrastructure modifications.

Built on top of the [Daruma](https://github.com/nshelton/spatiotemporal_db) spatiotemporal API (FastAPI + PostgreSQL + PostGIS).

---

## Core Principle

Every data source in your life reduces to the same thing: a timestamped entity with optional location, type, and metadata. The framework normalizes all sources into this universal shape, enriches entities without native GPS by cross-referencing Arc location history, and inserts them into a single Postgres table with a flexible JSONB payload column.

---

## Schema

One table. Never changes regardless of how many sources are added.

After all migrations (001–004), the live schema is:

```sql
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            VARCHAR(255) NOT NULL,          -- 'spotify', 'sleep', 'photo', etc.
    t_start         TIMESTAMPTZ NOT NULL,           -- when it started
    t_end           TIMESTAMPTZ,                    -- when it ended (null = instantaneous)
    lat             DOUBLE PRECISION,               -- WGS84 latitude
    lon             DOUBLE PRECISION,               -- WGS84 longitude
    geom            GEOMETRY(POINT, 4326),          -- auto-computed from lat/lon via trigger
    t_range         TSTZRANGE,                      -- auto-computed from t_start/t_end via trigger
    name            TEXT,                           -- display label
    color           VARCHAR(7),                     -- #RRGGBB for frontend rendering
    render_offset   DOUBLE PRECISION DEFAULT 0.0,   -- vertical offset in timeline view
    source          VARCHAR(255),                   -- origin system: 'spotify', 'arc', 'photos'
    external_id     VARCHAR(255),                   -- dedup key from origin system
    loc_source      VARCHAR(20),                    -- 'native' or 'inferred' (from Arc)
    payload         JSONB,                          -- source-specific data, anything goes
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Idempotent upserts: (source, external_id) is unique when both are non-null
CREATE UNIQUE INDEX idx_entities_source_external
    ON entities(source, external_id)
    WHERE source IS NOT NULL AND external_id IS NOT NULL;

-- Time overlap queries via GiST on computed range
CREATE INDEX idx_entities_t_range ON entities USING GIST (t_range);

-- Composite type+time for filtered timeline queries
CREATE INDEX idx_entities_type_time ON entities(type, t_start DESC);

-- Spatial bounding box queries
CREATE INDEX idx_entities_geom ON entities USING GIST (geom);

-- JSONB payload queries (e.g. payload->>'artist' = 'Radiohead')
CREATE INDEX idx_entities_payload ON entities USING GIN (payload);
```

### Triggers

Two triggers keep derived columns in sync automatically:

```sql
-- Auto-compute geom from lat/lon on insert or update
CREATE OR REPLACE FUNCTION update_geom_from_latlon()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.geom = ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326);
    ELSE
        NEW.geom = NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON entities
    FOR EACH ROW EXECUTE FUNCTION update_geom_from_latlon();

-- Auto-compute t_range from t_start/t_end on insert or update
CREATE OR REPLACE FUNCTION update_trange_from_timestamps()
RETURNS TRIGGER AS $$
BEGIN
    NEW.t_range = tstzrange(NEW.t_start, COALESCE(NEW.t_end, NEW.t_start), '[]');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trigger_update_trange
    BEFORE INSERT OR UPDATE OF t_start, t_end ON entities
    FOR EACH ROW EXECUTE FUNCTION update_trange_from_timestamps();
```

**Why JSONB `payload`?** Spotify metadata (track, artist, album) looks nothing like sleep data (stage, duration) or transaction data (merchant, amount, category). JSONB lets them coexist with zero schema changes while still being queryable via the GIN index.

**Why separate `lat`/`lon` + computed `geom`?** The API works with simple floats. The trigger builds the PostGIS geometry transparently. No application code ever touches `geom` directly — it's a query optimization detail.

**Why `t_range`?** Range overlap queries (`t_range && tstzrange(...)`) with a GiST index are far faster than compound `t_start`/`t_end` comparisons, especially for spans (sleep sessions, calendar events) that overlap a query window.

---

## Watermark State Table

Each source tracks the timestamp of its last successful run. The engine only processes items newer than the watermark, making re-runs cheap and idempotent.

```sql
CREATE TABLE IF NOT EXISTS source_state (
    source      VARCHAR(255) PRIMARY KEY,       -- matches entities.source
    last_run    TIMESTAMPTZ NOT NULL,
    last_count  INTEGER DEFAULT 0,              -- entities ingested in last run
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
```

---

## Location Enrichment

Most sources have time but not location. Arc knows where you were at every moment. A Postgres function bridges the gap:

```sql
CREATE FUNCTION infer_location(query_ts TIMESTAMPTZ)
RETURNS TABLE(lat DOUBLE PRECISION, lon DOUBLE PRECISION) AS $$
    SELECT lat, lon FROM entities
    WHERE type = 'location.gps'
      AND source = 'arc'
      AND t_start <= query_ts
    ORDER BY t_start DESC
    LIMIT 1;
$$ LANGUAGE sql STABLE;
```

At insert time, entities without native GPS are enriched by asking "where was I at that timestamp?" from Arc data. The `loc_source` column tracks provenance: `'native'` if the source provided GPS (e.g. photo EXIF), `'inferred'` if backfilled from Arc.

---

## Source Plugin Contract

Each data source is a single Python file implementing two methods:

```python
# engine/base.py
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterator, Any

@dataclass
class Entity:
    """Normalized entity ready for database insertion."""
    type: str                                   # e.g. 'spotify', 'photo', 'sleep'
    t_start: datetime
    t_end: datetime | None = None               # null = instantaneous
    lat: float | None = None                    # WGS84
    lon: float | None = None                    # WGS84
    name: str | None = None                     # display label
    color: str | None = None                    # #RRGGBB
    external_id: str | None = None              # dedup key from origin
    payload: dict | None = None                 # source-specific data

class Source(ABC):
    name: str           # identifier used in entities.source column
    schedule: str       # cron expression for automatic runs

    @abstractmethod
    def discover(self, since: datetime) -> Iterator[Any]:
        """Yield raw items that are new since `since` (the last successful run)."""

    @abstractmethod
    def extract(self, raw: Any) -> Entity | list[Entity]:
        """Transform one raw item into one or more normalized Entities."""

    def has_native_location(self) -> bool:
        """Override to return True if this source provides its own GPS coords."""
        return False
```

That's the entire interface. Each source only knows how to read its own format. The engine handles scheduling, dedup, location enrichment, and Postgres insertion.

---

## Example Sources

### Spotify (API-based, no native location)

```python
class SpotifySource(Source):
    name = "spotify"
    schedule = "0 */6 * * *"

    def discover(self, since):
        return spotify.recently_played(after=int(since.timestamp() * 1000))

    def extract(self, track):
        played_at = parse(track['played_at'])
        duration = timedelta(milliseconds=track['track']['duration_ms'])
        return Entity(
            type='music',
            t_start=played_at,
            t_end=played_at + duration,
            external_id=f"{track['played_at']}_{track['track']['id']}",
            name=track['track']['name'],
            payload={
                'track': track['track']['name'],
                'artist': track['track']['artists'][0]['name'],
                'album': track['track']['album']['name'],
            }
        )
```

### Sleep (Apple Health XML export)

```python
class SleepSource(Source):
    name = "sleep"
    schedule = "0 8 * * *"

    def discover(self, since):
        tree = ET.parse(HEALTH_EXPORT_PATH)
        for record in tree.findall('.//Record[@type="HKCategoryTypeIdentifierSleepAnalysis"]'):
            start = parse(record.get('startDate'))
            if start > since:
                yield record

    def extract(self, record):
        start = parse(record.get('startDate'))
        end = parse(record.get('endDate'))
        return Entity(
            type='sleep',
            t_start=start,
            t_end=end,
            external_id=f"sleep_{start.isoformat()}",
            payload={
                'stage': record.get('value', '').split('.')[-1],
            }
        )
```

### Photos (filesystem scan, has native GPS)

```python
class PhotoSource(Source):
    name = "photos"
    schedule = "0 2 * * *"

    def has_native_location(self):
        return True

    def discover(self, since):
        for path in PHOTO_DIR.rglob('*.jpg'):
            if datetime.fromtimestamp(path.stat().st_mtime) > since:
                yield path

    def extract(self, path):
        exif = get_exif(path)
        return Entity(
            type='photo',
            t_start=exif.get('DateTimeOriginal', datetime.fromtimestamp(path.stat().st_mtime)),
            lat=exif.get('GPSLatitude'),
            lon=exif.get('GPSLongitude'),
            external_id=str(path),
            name=path.name,
            payload={
                'filename': path.name,
                'camera': exif.get('Model'),
                'dimensions': f"{exif.get('ImageWidth')}x{exif.get('ImageHeight')}",
            }
        )
```

### Google Calendar (API-based)

```python
class GoogleCalendarSource(Source):
    name = "google_calendar"
    schedule = "0 */4 * * *"

    def discover(self, since):
        return gcal_service.events().list(
            calendarId='primary', timeMin=since.isoformat(), singleEvents=True
        ).execute().get('items', [])

    def extract(self, event):
        start = parse(event['start'].get('dateTime', event['start'].get('date')))
        end = parse(event['end'].get('dateTime', event['end'].get('date')))
        return Entity(
            type='calendar',
            t_start=start,
            t_end=end,
            external_id=event['id'],
            name=event.get('summary', ''),
            payload={
                'description': event.get('description', ''),
                'attendees': [a['email'] for a in event.get('attendees', [])],
                'location_text': event.get('location', ''),
            }
        )
```

### Monarch Money (CSV export)

```python
class MonarchSource(Source):
    name = "monarch"
    schedule = "0 6 * * *"

    def discover(self, since):
        for row in csv.DictReader(open(MONARCH_CSV)):
            if parse(row['Date']) > since:
                yield row

    def extract(self, row):
        return Entity(
            type='transaction',
            t_start=parse(row['Date']),
            external_id=row.get('Transaction ID', f"{row['Date']}_{row['Merchant']}_{row['Amount']}"),
            name=row['Merchant'],
            payload={
                'merchant': row['Merchant'],
                'amount': float(row['Amount']),
                'category': row.get('Category', ''),
                'account': row.get('Account', ''),
            }
        )
```

---

## Engine (Auto-Discovery + Orchestration)

The runner automatically discovers Source subclasses by scanning the `sources/` directory and writes directly to the local Postgres instance using asyncpg.

```python
class TimelineEngine:
    def __init__(self, db_url: str):
        self.db_url = db_url
        self.pool = None
        self.sources = self._discover_sources()

    def _discover_sources(self):
        sources = {}
        for path in Path('sources').glob('*.py'):
            module = importlib.import_module(f'sources.{path.stem}')
            for obj in vars(module).values():
                if isinstance(obj, type) and issubclass(obj, Source) and obj is not Source:
                    instance = obj()
                    sources[instance.name] = instance
        return sources

    async def run_source(self, name: str):
        source = self.sources[name]
        since = await self._get_watermark(name)
        count = 0

        async with self.pool.acquire() as conn:
            for raw in source.discover(since):
                entities = source.extract(raw)
                if not isinstance(entities, list):
                    entities = [entities]
                for entity in entities:
                    # Location enrichment
                    if entity.lat is None and not source.has_native_location():
                        loc = await conn.fetchrow(
                            "SELECT lat, lon FROM entities "
                            "WHERE type = 'location.gps' AND source = 'arc' "
                            "AND t_start <= $1 ORDER BY t_start DESC LIMIT 1",
                            entity.t_start
                        )
                        if loc:
                            entity.lat, entity.lon = loc['lat'], loc['lon']
                            loc_source = 'inferred'
                        else:
                            loc_source = None
                    else:
                        loc_source = 'native' if entity.lat is not None else None

                    await conn.execute(UPSERT_SQL,
                        entity.type, entity.t_start, entity.t_end,
                        entity.lat, entity.lon,
                        entity.name, entity.color, entity.render_offset,
                        source.name, entity.external_id, loc_source,
                        json.dumps(entity.payload) if entity.payload else None,
                    )
                    count += 1

        await self._set_watermark(name, datetime.now(UTC), count)
```

### Upsert SQL

Matches the existing Daruma API pattern. The `lat`/`lon` → `geom` and `t_start`/`t_end` → `t_range` conversions happen automatically via database triggers.

```sql
INSERT INTO entities (
    type, t_start, t_end, lat, lon,
    name, color, render_offset,
    source, external_id, loc_source,
    payload
) VALUES (
    $1, $2, $3, $4, $5,
    $6, $7, $8,
    $9, $10, $11,
    $12::jsonb
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
    loc_source = EXCLUDED.loc_source,
    payload = EXCLUDED.payload,
    updated_at = now()
RETURNING id, (xmax = 0) AS inserted;
```

**Watermarks**: each source tracks the timestamp of its last successful run in the `source_state` table. `discover()` only yields items newer than the watermark, so re-runs are cheap. The UPSERT on `(source, external_id)` makes everything idempotent.

---

## CLI

```bash
python timeline.py status                              # all sources, last run, entity counts
python timeline.py run spotify                         # manual trigger one source
python timeline.py run --all                           # trigger all sources
python timeline.py backfill photos --since 2019-01-01  # historical import
python timeline.py scaffold newsource                  # generate template source file
python timeline.py tail                                # live stream of entities being ingested
python timeline.py query --source sleep --last 7d      # quick data inspection
```

---

## Where LLMs Fit

Keep LLMs **out of the hot path** — deterministic extractors are faster, cheaper, and debuggable.

Two places where LLMs add genuine value:

1. **Source scaffolding (one-time)**: Feed a sample of raw data to an LLM and have it generate the Source class. "Here's what a Monarch CSV row looks like, write me a source plugin." Saves the tedious part of writing each adapter.

2. **Unstructured imports (special source type)**: For one-off things like "500 journal entries in random text files with dates mentioned somewhere in the body." Create an `LLMSource` base class that uses a local model (Ollama/llama.cpp) for the `extract()` step instead of deterministic parsing. This is the only source type where the extraction logic isn't hardcoded.

---

## Planned Sources

| Source | Type String | Data | Location | Schedule | Effort |
|---|---|---|---|---|---|
| Arc | `location.gps` | GPS coordinates | Native | Hourly | Done (existing) |
| Photos | `photo` | EXIF metadata | Native (EXIF GPS) | Daily 2am | Low |
| Sleep Tracker | `sleep` | Sleep stages/duration via Apple Health | Inferred (home) | Daily 8am | Low |
| Google Calendar | `calendar` | Events, meetings | Inferred or text | Every 4h | Low |
| Spotify | `music` | Listening history | Inferred | Every 6h | Low |
| Monarch Money | `transaction` | Transactions | Inferred | Daily 6am | Medium (API) |
| Apple Health (broad) | `workout`, `steps`, `heartrate` | Workouts, steps, heart rate | Inferred/Native | Daily | Medium |
| Browser History | `browser` | URLs visited | Inferred | Daily | Low |
| Git Commits | `git_commit` | Code activity | Inferred | Daily | Low |
| Messages (metadata) | `message` | Timestamp + contact, no content | Inferred | Daily | Medium (Mac only) |
| Email (metadata) | `email` | Sender, subject, timestamp | Inferred | Daily | Medium (Gmail API) |
| Weather (enrichment) | `weather` | Historical conditions per location | N/A (enriches existing) | Post-hoc | Low |

---

## Project Structure

```
timeline/
  sources/               # drop a file here = new source. auto-discovered.
    arc.py
    photos.py
    sleep.py
    spotify.py
    google_cal.py
    monarch.py
    git_commits.py
    browser_history.py
  engine/
    base.py              # Source ABC, Entity dataclass
    runner.py            # auto-discovery, orchestration, upsert logic
    enrichment.py        # Arc location inference
    scheduler.py         # cron via APScheduler
    state.py             # watermarks per source (last run tracking)
  app/                   # Daruma API (existing)
    models.py            # Pydantic models (EntityIn, EntityOut, queries)
    routes/
      entity.py          # POST /v1/entity, /v1/entities/batch
      query.py           # POST /v1/query/time, /v1/query/bbox
    db.py                # asyncpg pool
    config.py            # DB URL, API key
    main.py              # FastAPI app
  migrations/
    001_initial.sql
    002_sample_data.sql
    003_fix_column_names.sql
    004_fix_trange.sql
    005_engine_additions.sql  # loc_source column, source_state table, GIN index
  config.py              # paths, db connection, API keys
  timeline.py            # CLI entry point
```

---

## Infrastructure

- **Server**: Windows desktop on LAN, always-on
- **Database**: PostgreSQL with PostGIS extension (local)
- **Access**: Engine writes directly to Postgres (same machine, no HTTP overhead)
- **API**: Daruma FastAPI server for frontend queries (React/Electron 3D visualization)
- **Scheduling**: APScheduler (in-process) or Windows Task Scheduler
- **File sync**: iCloud syncs Arc JSONs + photos to the server
- **Remote access**: Tailscale for secure access without port forwarding
- **File browsing**: Copyparty pointed at photo/data directories

---

## Key Design Decisions

- **One table, JSONB payload**: No per-source tables. New sources never require schema changes. GIN index keeps JSONB queries fast.
- **Entities, not events**: The `entities` table can hold both transient events (a song play, a transaction) and persistent objects (a place, a person) — more general than an events-only model.
- **Computed columns via triggers**: `geom` (from lat/lon) and `t_range` (from t_start/t_end) are maintained by triggers. Application code never touches them. Queries benefit from specialized indexes on these derived columns.
- **Direct DB access for ingestion**: The engine writes to Postgres directly (asyncpg), avoiding HTTP overhead. The FastAPI API exists for the frontend, not for ingestion.
- **Watermark-based incremental sync**: Each source tracks its last run in `source_state`. Only new data is processed. Safe to re-run at any time.
- **Idempotent upserts**: `UNIQUE(source, external_id)` means duplicate inserts are harmless.
- **Location as a first-class enrichment**: Arc is the spatial backbone. Any timestamped entity can be placed on the map even if the source has no GPS.
- **Plugin autodiscovery**: The engine imports all Source subclasses from the `sources/` directory at startup. No registration step.
- **Deterministic by default, LLM where useful**: Hardcoded parsers for structured sources, LLM fallback only for truly unstructured data.
- **UUID primary keys**: No sequence contention when multiple sources insert concurrently. No leaking of insert order.
- **Rendering columns as first-class**: `name`, `color`, `render_offset` live on the table (not buried in payload) so the 3D frontend can sort/filter on them directly.
