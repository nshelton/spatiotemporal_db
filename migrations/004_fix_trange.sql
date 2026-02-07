-- Fix t_range generation
-- PostgreSQL generated columns have some limitations, so we'll use a trigger instead

-- Drop the generated column
ALTER TABLE entities DROP COLUMN IF EXISTS t_range;

-- Add t_range as a regular column
ALTER TABLE entities ADD COLUMN IF NOT EXISTS t_range tstzrange;

-- Function to update t_range from t_start and t_end
CREATE OR REPLACE FUNCTION update_trange_from_timestamps()
RETURNS TRIGGER AS $$
BEGIN
    NEW.t_range = tstzrange(NEW.t_start, COALESCE(NEW.t_end, NEW.t_start), '[]');
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Trigger to update t_range when t_start or t_end changes
CREATE TRIGGER trigger_update_trange
    BEFORE INSERT OR UPDATE OF t_start, t_end ON entities
    FOR EACH ROW
    EXECUTE FUNCTION update_trange_from_timestamps();

-- Update existing rows to populate t_range
UPDATE entities SET t_start = t_start;

-- Recreate the index
DROP INDEX IF EXISTS idx_entities_t_range;
CREATE INDEX IF NOT EXISTS idx_entities_t_range ON entities USING GIST (t_range);

COMMENT ON COLUMN entities.t_range IS 'Time range from t_start to t_end (auto-updated via trigger)';
