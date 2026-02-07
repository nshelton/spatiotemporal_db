-- Fix column names to match backend expectations
-- The backend expects 'geom' (geometry) not 'location' (geography)
-- The backend expects 't_range' (tstzrange) computed from t_start and t_end

-- Drop the old trigger and column
DROP TRIGGER IF EXISTS trigger_update_location ON entities;
DROP FUNCTION IF EXISTS update_location_from_latlon();
ALTER TABLE entities DROP COLUMN IF EXISTS location;

-- Add geom column as PostGIS GEOMETRY (not GEOGRAPHY)
ALTER TABLE entities ADD COLUMN IF NOT EXISTS geom geometry(Point, 4326);

-- Add t_range as a generated column
ALTER TABLE entities ADD COLUMN IF NOT EXISTS t_range tstzrange
    GENERATED ALWAYS AS (tstzrange(t_start, COALESCE(t_end, t_start))) STORED;

-- Function to automatically set geom from lat/lon
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

-- Trigger to update geom when lat/lon changes
CREATE TRIGGER trigger_update_geom
    BEFORE INSERT OR UPDATE OF lat, lon ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_geom_from_latlon();

-- Update existing rows to populate geom
UPDATE entities SET lat = lat WHERE lat IS NOT NULL;

-- Recreate spatial index with correct column name
DROP INDEX IF EXISTS idx_entities_location;
CREATE INDEX IF NOT EXISTS idx_entities_geom ON entities USING GIST (geom);

-- Recreate time range index
DROP INDEX IF EXISTS idx_entities_time_range;
CREATE INDEX IF NOT EXISTS idx_entities_t_range ON entities USING GIST (t_range);

COMMENT ON COLUMN entities.geom IS 'PostGIS geometry point (auto-generated from lat/lon)';
COMMENT ON COLUMN entities.t_range IS 'Time range from t_start to t_end (auto-generated)';
