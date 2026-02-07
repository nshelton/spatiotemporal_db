-- Initial database schema for spatiotemporal entity database
-- Run with: psql postgresql://daruma:your-password@localhost/daruma -f migrations/001_initial.sql

-- Enable PostGIS extension for spatial operations
CREATE EXTENSION IF NOT EXISTS postgis;

-- Create entities table
CREATE TABLE IF NOT EXISTS entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type VARCHAR(255) NOT NULL,
    t_start TIMESTAMPTZ NOT NULL,
    t_end TIMESTAMPTZ,
    lat DOUBLE PRECISION,
    lon DOUBLE PRECISION,
    location GEOGRAPHY(POINT, 4326),  -- PostGIS spatial index
    name TEXT,
    color VARCHAR(7),  -- #RRGGBB format
    render_offset DOUBLE PRECISION DEFAULT 0.0,
    source VARCHAR(255),
    external_id VARCHAR(255),
    payload JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create indexes for efficient queries

-- Time-based queries
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_t_start ON entities(t_start);
CREATE INDEX IF NOT EXISTS idx_entities_t_end ON entities(t_end);
CREATE INDEX IF NOT EXISTS idx_entities_time_range ON entities USING GIST (tstzrange(t_start, COALESCE(t_end, t_start)));

-- Spatial queries (PostGIS GIST index)
CREATE INDEX IF NOT EXISTS idx_entities_location ON entities USING GIST (location);

-- Source-based idempotent upserts
CREATE UNIQUE INDEX IF NOT EXISTS idx_entities_source_external
    ON entities(source, external_id)
    WHERE source IS NOT NULL AND external_id IS NOT NULL;

-- Composite indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_entities_type_time ON entities(type, t_start DESC);

-- Function to automatically set location geography from lat/lon
CREATE OR REPLACE FUNCTION update_location_from_latlon()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.lat IS NOT NULL AND NEW.lon IS NOT NULL THEN
        NEW.location = ST_SetSRID(ST_MakePoint(NEW.lon, NEW.lat), 4326)::geography;
    ELSE
        NEW.location = NULL;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to update location geography when lat/lon changes
CREATE TRIGGER trigger_update_location
    BEFORE INSERT OR UPDATE OF lat, lon ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_location_from_latlon();

-- Function to update updated_at timestamp
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to automatically update updated_at
CREATE TRIGGER trigger_update_updated_at
    BEFORE UPDATE ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Grant permissions (adjust if needed)
-- GRANT ALL PRIVILEGES ON TABLE entities TO daruma;
-- GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO daruma;

COMMENT ON TABLE entities IS 'Spatiotemporal entities with optional location and time extent';
COMMENT ON COLUMN entities.type IS 'Entity type identifier (e.g., location.gps, photo, event)';
COMMENT ON COLUMN entities.t_start IS 'Start timestamp (UTC)';
COMMENT ON COLUMN entities.t_end IS 'End timestamp (UTC) for spans; null for instantaneous events';
COMMENT ON COLUMN entities.location IS 'PostGIS geography point (auto-generated from lat/lon)';
COMMENT ON COLUMN entities.source IS 'Source identifier for idempotent upserts';
COMMENT ON COLUMN entities.external_id IS 'External ID from source system';
COMMENT ON COLUMN entities.payload IS 'Type-specific JSON data';
