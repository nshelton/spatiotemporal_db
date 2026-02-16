-- Daruma Spatiotemporal Database Schema
-- Single-table design with JSONB payload for extensibility
-- Compatible with Timeline Engine (ingestion) and Daruma API (queries)

-- Enable PostGIS for spatial operations
CREATE EXTENSION IF NOT EXISTS postgis;

-- Main entities table
CREATE TABLE IF NOT EXISTS entities (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type            VARCHAR(255) NOT NULL,
    t_start         TIMESTAMPTZ NOT NULL,
    t_end           TIMESTAMPTZ,
    lat             DOUBLE PRECISION,
    lon             DOUBLE PRECISION,
    geom            GEOMETRY(POINT, 4326),      -- auto-computed from lat/lon via trigger
    t_range         TSTZRANGE,                  -- auto-computed from t_start/t_end via trigger
    name            TEXT,
    color           VARCHAR(7),
    render_offset   DOUBLE PRECISION DEFAULT 0.0,
    source          VARCHAR(255),
    external_id     VARCHAR(255),
    loc_source      VARCHAR(20),                -- 'native' or 'inferred'
    payload         JSONB,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for efficient queries

-- Idempotent upserts: (source, external_id) is unique when both are non-null
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_source_external
    ON entities(source, external_id)
    WHERE source IS NOT NULL AND external_id IS NOT NULL;

-- Time overlap queries via GiST on computed range
CREATE INDEX IF NOT EXISTS idx_entities_t_range ON entities USING GIST (t_range);

-- Type-based filtering
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

-- Composite type+time for filtered timeline queries
CREATE INDEX IF NOT EXISTS idx_entities_type_time ON entities(type, t_start DESC);

-- Spatial bounding box queries
CREATE INDEX IF NOT EXISTS idx_entities_geom ON entities USING GIST (geom);

-- JSONB payload queries (e.g. payload->>'artist' = 'Radiohead')
CREATE INDEX IF NOT EXISTS idx_entities_payload ON entities USING GIN (payload);

-- Simple time-based queries
CREATE INDEX IF NOT EXISTS idx_entities_t_start ON entities(t_start);
CREATE INDEX IF NOT EXISTS idx_entities_t_end ON entities(t_end);

-- Watermark state table for incremental ingestion
CREATE TABLE IF NOT EXISTS source_state (
    source      VARCHAR(255) PRIMARY KEY,
    last_run    TIMESTAMPTZ NOT NULL,
    last_count  INTEGER DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);

-- Trigger function: auto-compute geom from lat/lon
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

-- Trigger function: auto-compute t_range from t_start/t_end
CREATE OR REPLACE FUNCTION update_trange_from_timestamps()
RETURNS TRIGGER AS $$
BEGIN
    NEW.t_range = tstzrange(NEW.t_start, COALESCE(NEW.t_end, NEW.t_start), '[]');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger function: update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger: update geom when lat/lon changes
DROP TRIGGER IF EXISTS trigger_update_geom ON entities;
CREATE TRIGGER trigger_update_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_geom_from_latlon();

-- Trigger: update t_range when t_start/t_end changes
DROP TRIGGER IF EXISTS trigger_update_trange ON entities;
CREATE TRIGGER trigger_update_trange
    BEFORE INSERT OR UPDATE OF t_start, t_end ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_trange_from_timestamps();

-- Trigger: update updated_at on changes
DROP TRIGGER IF EXISTS trigger_update_updated_at ON entities;
CREATE TRIGGER trigger_update_updated_at
    BEFORE UPDATE ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions to daruma user
GRANT ALL PRIVILEGES ON TABLE entities TO daruma;
GRANT ALL PRIVILEGES ON TABLE source_state TO daruma;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO daruma;

-- Comments for documentation
COMMENT ON TABLE entities IS 'Spatiotemporal entities with optional location and time extent';
COMMENT ON COLUMN entities.type IS 'Entity type identifier (e.g., location.gps, photo, music)';
COMMENT ON COLUMN entities.t_start IS 'Start timestamp (UTC)';
COMMENT ON COLUMN entities.t_end IS 'End timestamp (UTC) for spans; null for instantaneous events';
COMMENT ON COLUMN entities.geom IS 'PostGIS geometry point (auto-generated from lat/lon)';
COMMENT ON COLUMN entities.t_range IS 'Computed time range for efficient overlap queries';
COMMENT ON COLUMN entities.source IS 'Source identifier for idempotent upserts';
COMMENT ON COLUMN entities.external_id IS 'External ID from source system';
COMMENT ON COLUMN entities.loc_source IS 'Location provenance: native (from source) or inferred (from Arc)';
COMMENT ON COLUMN entities.payload IS 'Type-specific JSON data';