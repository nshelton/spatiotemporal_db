-- Engine additions: loc_source tracking, source watermarks, payload index
-- Run with: psql postgresql://daruma:your-password@localhost/daruma -f migrations/005_engine_additions.sql

-- Track whether location was native (from source) or inferred (from Arc)
ALTER TABLE entities ADD COLUMN IF NOT EXISTS loc_source VARCHAR(20);
COMMENT ON COLUMN entities.loc_source IS 'Location provenance: native (from source) or inferred (from Arc)';

-- Backfill: any entity with lat/lon that came from arc is native GPS
UPDATE entities SET loc_source = 'native' WHERE lat IS NOT NULL AND loc_source IS NULL;

-- GIN index on payload for queries like payload->>'artist' = 'Radiohead'
CREATE INDEX IF NOT EXISTS idx_entities_payload ON entities USING GIN (payload);

-- Source watermark table for incremental sync
CREATE TABLE IF NOT EXISTS source_state (
    source      VARCHAR(255) PRIMARY KEY,
    last_run    TIMESTAMPTZ NOT NULL,
    last_count  INTEGER DEFAULT 0,
    updated_at  TIMESTAMPTZ DEFAULT NOW()
);
COMMENT ON TABLE source_state IS 'Tracks last successful run per ingestion source';

-- Helper: infer location from Arc GPS data for a given timestamp
CREATE OR REPLACE FUNCTION infer_location(query_ts TIMESTAMPTZ)
RETURNS TABLE(lat DOUBLE PRECISION, lon DOUBLE PRECISION) AS $$
SELECT lat, lon FROM entities
WHERE type = 'location.gps'
AND source = 'arc'
AND t_start <= query_ts
ORDER BY t_start DESC
LIMIT 1;
$$ LANGUAGE sql STABLE;
COMMENT ON FUNCTION infer_location IS 'Find nearest Arc GPS point at or before a given timestamp';
