-- Sample data for testing the spatiotemporal database
-- Run with: psql postgresql://daruma:your-password@localhost/daruma -f migrations/002_sample_data.sql

-- Insert sample GPS location data points around Los Angeles
INSERT INTO entities (type, t_start, t_end, lat, lon, name, color, payload) VALUES
    ('location.gps', '2024-01-15 08:00:00+00', NULL, 34.0522, -118.2437, 'Downtown LA', '#FF0000', '{"accuracy_m": 5.2}'),
    ('location.gps', '2024-01-15 08:15:00+00', NULL, 34.0689, -118.4452, 'Santa Monica', '#FF0000', '{"accuracy_m": 8.1}'),
    ('location.gps', '2024-01-15 08:30:00+00', NULL, 34.1016, -118.3409, 'Hollywood', '#FF0000', '{"accuracy_m": 6.5}'),
    ('location.gps', '2024-01-15 08:45:00+00', NULL, 34.0736, -118.2402, 'Echo Park', '#FF0000', '{"accuracy_m": 7.3}'),
    ('location.gps', '2024-01-15 09:00:00+00', NULL, 34.1184, -118.3004, 'Griffith Park', '#FF0000', '{"accuracy_m": 4.8}'),
    ('location.gps', '2024-01-15 09:15:00+00', NULL, 34.0407, -118.2468, 'Arts District', '#FF0000', '{"accuracy_m": 5.9}'),
    ('location.gps', '2024-01-15 09:30:00+00', NULL, 34.0259, -118.7798, 'Malibu Beach', '#FF0000', '{"accuracy_m": 12.3}'),
    ('location.gps', '2024-01-15 09:45:00+00', NULL, 34.0195, -118.4912, 'Venice Beach', '#FF0000', '{"accuracy_m": 9.7}');

-- Add some spread out points for better visibility
INSERT INTO entities (type, t_start, t_end, lat, lon, name, color, payload) VALUES
    ('location.gps', '2024-01-15 10:00:00+00', NULL, 34.0000, -118.2000, 'Point A', '#00FF00', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 10:15:00+00', NULL, 34.0500, -118.2000, 'Point B', '#00FF00', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 10:30:00+00', NULL, 34.1000, -118.2000, 'Point C', '#00FF00', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 10:45:00+00', NULL, 34.0000, -118.2500, 'Point D', '#0000FF', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 11:00:00+00', NULL, 34.0500, -118.2500, 'Point E', '#0000FF', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 11:15:00+00', NULL, 34.1000, -118.2500, 'Point F', '#0000FF', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 11:30:00+00', NULL, 34.0000, -118.3000, 'Point G', '#FFFF00', '{"accuracy_m": 5.0}'),
    ('location.gps', '2024-01-15 11:45:00+00', NULL, 34.0500, -118.3000, 'Point H', '#FFFF00', '{"accuracy_m": 5.0}');

-- Add some event spans (not just points)
INSERT INTO entities (type, t_start, t_end, lat, lon, name, color, render_offset, payload) VALUES
    ('event', '2024-01-15 09:00:00+00', '2024-01-15 12:00:00+00', 34.0522, -118.2437, 'Morning Meeting', '#FF00FF', 0.0, '{"participants": 5}'),
    ('event', '2024-01-15 13:00:00+00', '2024-01-15 15:00:00+00', 34.0689, -118.4452, 'Lunch Break', '#00FFFF', 0.1, '{"location": "restaurant"}'),
    ('event', '2024-01-15 16:00:00+00', '2024-01-15 18:00:00+00', 34.1016, -118.3409, 'Project Work', '#FFA500', 0.2, '{"project": "daruma"}');

-- Verify the data was inserted
SELECT COUNT(*) as total_entities FROM entities;
SELECT type, COUNT(*) as count FROM entities GROUP BY type;
