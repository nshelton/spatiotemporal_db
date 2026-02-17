# Place Detection & Visit Tracking — Design Document

Automatic discovery of significant locations from GPS data using DBSCAN clustering, with time-bound visit detection and user-friendly place naming.

**Status**: Design Phase
**Dependencies**: Timeline Engine v2, location.gps entities
**Target**: Millions of GPS points → Dozens of named places + thousands of visits

---

## Overview

Transform raw GPS data into semantic location understanding:

1. **DBSCAN Clustering**: Analyze `location.gps` entities to discover clusters (places you frequently visit)
2. **Place Entities**: Create persistent `place` entities with unique IDs and default names
3. **Visit Detection**: Identify time spans when you were at each place
4. **Visit Entities**: Create `place.visit` entities with temporal bounds
5. **User Naming**: API endpoint to rename places, propagating to all visits

**Key Design Goal**: Easy iteration during development — drop all visits, re-run on subsets, refine algorithms without data loss.

---

## Entity Schema

### `place` Entity

Represents a discovered significant location (persistent, no time span).

```typescript
{
  id: UUID,                    // Database-generated
  type: "place",
  t_start: null,               // Places are timeless
  t_end: null,
  lat: 34.0522,                // Cluster centroid (WGS84)
  lon: -118.2437,
  name: "place-a3f8e9d2",      // Initial: GUID slug, user renames
  color: "#FF5722",            // Auto-assigned or user-set
  render_offset: 0.0,
  source: "place_detector",
  external_id: "cluster_42",   // DBSCAN cluster ID (for dedup)
  loc_source: "inferred",      // Derived from GPS clusters
  payload: {
    cluster_id: 42,
    radius_meters: 50.3,       // 95th percentile distance from centroid
    point_count: 1247,         // GPS samples in cluster
    first_seen: "2024-01-01T12:34:00Z",
    last_seen: "2026-02-16T08:15:00Z",
    visit_count: 342,          // Number of place.visit entities
    total_dwell_hours: 1520.5, // Total time spent here
    dbscan_eps: 50,            // Clustering parameters used
    dbscan_min_samples: 10,
    confidence: 0.95,          // Clustering quality metric
    version: 1                 // Algorithm version (for migration)
  }
}
```

**Indexes**:
- `(source, external_id)` for upsert (already exists)
- `type = 'place'` for place queries

### `place.visit` Entity

Represents a time-bound session at a place.

```typescript
{
  id: UUID,
  type: "place.visit",
  t_start: "2026-02-16T14:30:00Z",  // Arrival time
  t_end: "2026-02-16T18:45:00Z",    // Departure time
  lat: 34.0522,                     // Place centroid (denormalized)
  lon: -118.2437,
  name: "Home",                     // Denormalized from place (for queries)
  color: "#FF5722",                 // Denormalized from place
  render_offset: 0.0,
  source: "place_detector",
  external_id: "visit_2026-02-16T14:30:00Z_cluster_42",  // Dedup key
  loc_source: "inferred",
  payload: {
    place_id: "uuid-of-place-entity",  // Foreign key to place
    place_external_id: "cluster_42",    // For easier joins
    dwell_time_minutes: 255,
    entry_time: "2026-02-16T14:30:00Z",
    exit_time: "2026-02-16T18:45:00Z",
    gps_point_count: 47,         // GPS samples during visit
    movement_radius_meters: 18.5, // Max distance from centroid
    entry_speed_kmh: 35.2,       // Speed when entering
    exit_speed_kmh: 12.1,        // Speed when exiting
    gap_before_minutes: 120,     // Time since last visit
    version: 1                   // Algorithm version
  }
}
```

**Indexes**:
- `(source, external_id)` for upsert
- `type = 'place.visit'` for visit queries
- `t_range` for temporal queries (already exists)
- `(payload->>'place_id')` GIN index for joins

---

## DBSCAN Clustering Algorithm

### Parameters

```python
# Tunable parameters
EPS_METERS = 50          # Maximum distance to be in same cluster (50m radius)
MIN_SAMPLES = 10         # Minimum GPS points to form a cluster
MIN_VISIT_COUNT = 3      # Minimum visits to qualify as significant place
MIN_TOTAL_DWELL_HOURS = 1.0  # Minimum total time spent
```

### Pipeline

```python
def discover_places(time_window=None, sample_limit=None):
    """
    Run DBSCAN clustering on location.gps data.

    Args:
        time_window: Optional (start, end) tuple for subset testing
        sample_limit: Optional limit for development speed

    Returns:
        List of place entities to insert
    """
    # 1. Load GPS data from database
    gps_points = load_gps_data(time_window, sample_limit)
    # Result: [(lat, lon, timestamp), ...]

    # 2. Convert to radians for haversine distance
    coords = np.radians([(p.lat, p.lon) for p in gps_points])

    # 3. Run DBSCAN with haversine metric
    # eps in radians: eps_meters / earth_radius_meters
    eps_radians = EPS_METERS / 6371000
    clustering = DBSCAN(
        eps=eps_radians,
        min_samples=MIN_SAMPLES,
        metric='haversine'
    ).fit(coords)

    # 4. Analyze clusters
    places = []
    for cluster_id in set(clustering.labels_):
        if cluster_id == -1:  # Noise
            continue

        cluster_points = [p for i, p in enumerate(gps_points)
                         if clustering.labels_[i] == cluster_id]

        # Calculate cluster properties
        centroid = calculate_centroid(cluster_points)
        radius = calculate_radius_95percentile(cluster_points, centroid)

        # Filter by significance
        if len(cluster_points) < MIN_SAMPLES:
            continue

        # Create place entity
        place = {
            "type": "place",
            "lat": centroid.lat,
            "lon": centroid.lon,
            "name": f"place-{generate_short_guid()}",  # e.g., "place-a3f8e9d2"
            "color": assign_color(cluster_id),
            "source": "place_detector",
            "external_id": f"cluster_{cluster_id}",
            "loc_source": "inferred",
            "payload": {
                "cluster_id": cluster_id,
                "radius_meters": radius,
                "point_count": len(cluster_points),
                "first_seen": min(p.timestamp for p in cluster_points),
                "last_seen": max(p.timestamp for p in cluster_points),
                "dbscan_eps": EPS_METERS,
                "dbscan_min_samples": MIN_SAMPLES,
                "version": 1
            }
        }
        places.append(place)

    return places
```

### Centroid Calculation

```python
def calculate_centroid(points):
    """Calculate geographic centroid using mean of coordinates."""
    # Simple mean (good enough for small clusters < 50m)
    lat = np.mean([p.lat for p in points])
    lon = np.mean([p.lon for p in points])
    return (lat, lon)
```

### Radius Calculation

```python
def calculate_radius_95percentile(points, centroid):
    """Calculate 95th percentile distance from centroid."""
    distances = [haversine_distance(p, centroid) for p in points]
    return np.percentile(distances, 95)
```

---

## Visit Detection Algorithm

### Parameters

```python
# Tunable parameters
MAX_GAP_MINUTES = 10        # Max time gap to still be same visit
MIN_DWELL_MINUTES = 5       # Minimum time to count as visit
MAX_SPEED_KMH = 5           # Max speed to be considered stationary
```

### Pipeline

```python
def detect_visits(places, time_window=None):
    """
    Detect visits by analyzing GPS timeline against known places.

    Args:
        places: List of place entities with centroids and radii
        time_window: Optional (start, end) for subset processing

    Returns:
        List of place.visit entities to insert
    """
    visits = []

    # Load GPS data sorted by time
    gps_timeline = load_gps_timeline(time_window)

    # For each place, scan timeline for visits
    for place in places:
        place_visits = []
        current_visit = None

        for point in gps_timeline:
            distance = haversine_distance(point, place.centroid)
            is_at_place = distance <= place.radius_meters

            if is_at_place:
                if current_visit is None:
                    # Start new visit
                    current_visit = {
                        "entry_time": point.timestamp,
                        "points": [point]
                    }
                else:
                    # Continue visit
                    time_gap = (point.timestamp - current_visit["points"][-1].timestamp).seconds / 60

                    if time_gap <= MAX_GAP_MINUTES:
                        current_visit["points"].append(point)
                    else:
                        # Gap too large, end previous visit and start new one
                        place_visits.append(finalize_visit(current_visit, place))
                        current_visit = {
                            "entry_time": point.timestamp,
                            "points": [point]
                        }
            else:
                # Left the place
                if current_visit is not None:
                    place_visits.append(finalize_visit(current_visit, place))
                    current_visit = None

        # Handle ongoing visit
        if current_visit is not None:
            place_visits.append(finalize_visit(current_visit, place))

        # Filter by minimum dwell time
        visits.extend([v for v in place_visits
                      if v.dwell_time_minutes >= MIN_DWELL_MINUTES])

    return visits

def finalize_visit(visit_data, place):
    """Convert raw visit data to place.visit entity."""
    points = visit_data["points"]
    entry_time = points[0].timestamp
    exit_time = points[-1].timestamp
    dwell_minutes = (exit_time - entry_time).seconds / 60

    return {
        "type": "place.visit",
        "t_start": entry_time,
        "t_end": exit_time,
        "lat": place.lat,
        "lon": place.lon,
        "name": place.name,  # Will be denormalized
        "color": place.color,
        "source": "place_detector",
        "external_id": f"visit_{entry_time.isoformat()}_cluster_{place.cluster_id}",
        "loc_source": "inferred",
        "payload": {
            "place_id": place.id,
            "place_external_id": place.external_id,
            "dwell_time_minutes": dwell_minutes,
            "entry_time": entry_time,
            "exit_time": exit_time,
            "gps_point_count": len(points),
            "movement_radius_meters": max(
                haversine_distance(p, place.centroid) for p in points
            ),
            "version": 1
        }
    }
```

---

## API Endpoints

### 1. Rename Place

Update a place name and propagate to all visits.

**Endpoint**: `PATCH /v1/places/{place_id}`

**Request**:
```typescript
{
  name: string;       // New human-readable name
  color?: string;     // Optional: update color too
}
```

**Response**:
```typescript
{
  place_id: string;
  name: string;
  updated_visits: number;  // Count of visits updated
}
```

**Implementation**:
```sql
-- Update place entity
UPDATE entities
SET name = $2, color = COALESCE($3, color), updated_at = now()
WHERE id = $1 AND type = 'place'
RETURNING id, name;

-- Update all visits for this place (denormalized name/color)
UPDATE entities
SET name = $2, color = COALESCE($3, color), updated_at = now()
WHERE type = 'place.visit'
  AND payload->>'place_id' = $1::text
RETURNING id;
```

### 2. List Places

Get all discovered places with stats.

**Endpoint**: `GET /v1/places`

**Query Params**:
- `min_visits`: Minimum visit count (default: 3)
- `order`: `visit_count_desc` | `last_seen_desc` | `name_asc`
- `limit`: Max results (default: 100)

**Response**:
```typescript
{
  places: Array<{
    id: string;
    name: string;
    lat: number;
    lon: number;
    color: string;
    visit_count: number;
    total_dwell_hours: number;
    first_seen: string;
    last_seen: string;
  }>
}
```

### 3. Get Place Details

Get a specific place with recent visits.

**Endpoint**: `GET /v1/places/{place_id}`

**Response**:
```typescript
{
  place: {
    id: string;
    name: string;
    lat: number;
    lon: number;
    color: string;
    radius_meters: number;
    visit_count: number;
    total_dwell_hours: number;
  };
  recent_visits: Array<{
    id: string;
    t_start: string;
    t_end: string;
    dwell_time_minutes: number;
  }>;
}
```

### 4. Delete All Visits

Clear all `place.visit` entities for algorithm iteration.

**Endpoint**: `DELETE /v1/visits`

**Query Params**:
- `confirm`: Must be `"yes"` (safety check)
- `version`: Optional version filter (e.g., delete only v1 visits)

**Response**:
```typescript
{
  deleted: number;
}
```

**Implementation**:
```sql
-- Delete all visits
DELETE FROM entities
WHERE type = 'place.visit'
  AND ($1::int IS NULL OR payload->>'version' = $1::text)
RETURNING id;
```

---

## Database Operations

### Drop All Visits

Quick reset during development:

```sql
-- Option 1: Delete all visits (leaves places intact)
DELETE FROM entities WHERE type = 'place.visit';

-- Option 2: Delete visits from specific algorithm version
DELETE FROM entities
WHERE type = 'place.visit'
  AND payload->>'version' = '1';

-- Option 3: Delete visits in time range (for subset re-processing)
DELETE FROM entities
WHERE type = 'place.visit'
  AND t_start >= '2026-02-01T00:00:00Z'
  AND t_start < '2026-03-01T00:00:00Z';
```

### Query Places by Visit Frequency

```sql
-- Top 10 most visited places
SELECT
  id,
  name,
  lat,
  lon,
  (payload->>'visit_count')::int AS visits,
  (payload->>'total_dwell_hours')::numeric AS total_hours
FROM entities
WHERE type = 'place'
ORDER BY (payload->>'visit_count')::int DESC
LIMIT 10;
```

### Update Place Name and Propagate

```sql
-- Update place
UPDATE entities
SET name = 'Home', updated_at = now()
WHERE id = 'place-uuid'
  AND type = 'place';

-- Update all visits
UPDATE entities
SET name = 'Home', updated_at = now()
WHERE type = 'place.visit'
  AND payload->>'place_id' = 'place-uuid';
```

### Get Visit Statistics

```sql
-- Visit frequency by place
SELECT
  p.name,
  COUNT(v.id) AS visit_count,
  SUM((v.payload->>'dwell_time_minutes')::numeric) / 60.0 AS total_hours
FROM entities p
LEFT JOIN entities v ON v.type = 'place.visit'
  AND v.payload->>'place_id' = p.id::text
WHERE p.type = 'place'
GROUP BY p.id, p.name
ORDER BY visit_count DESC;
```

---

## Development Workflow

### Phase 1: Initial Clustering (Subset Testing)

Test on small dataset for fast iteration:

```python
# Test on one month of data
places = discover_places(
    time_window=("2026-01-01", "2026-01-31"),
    sample_limit=10000  # ~10k points for fast testing
)

# Insert to database
insert_entities(places)

# Verify in viewer
```

### Phase 2: Visit Detection (Subset)

Test visit detection on same subset:

```python
# Get places discovered in Phase 1
places = load_places()

# Detect visits in same time window
visits = detect_visits(
    places,
    time_window=("2026-01-01", "2026-01-31")
)

# Insert to database
insert_entities(visits)
```

### Phase 3: Algorithm Refinement

Iterate on parameters:

```python
# Drop previous visits
delete_all_visits()

# Try new parameters
EPS_METERS = 75  # Increase cluster radius
MIN_DWELL_MINUTES = 10  # Longer minimum stay

# Re-run visit detection
visits = detect_visits(places, time_window=("2026-01-01", "2026-01-31"))
insert_entities(visits)

# Compare results in viewer
```

### Phase 4: Full Dataset Processing

Once satisfied with algorithm:

```python
# Full clustering (no time window, no limit)
places = discover_places()
insert_entities(places)

# Full visit detection
visits = detect_visits(places)
insert_entities(visits)  # May take minutes for millions of points
```

### Phase 5: User Naming

Use viewer to rename places:

```javascript
// In viewer, when user renames a place
await fetch(`/v1/places/${placeId}`, {
  method: 'PATCH',
  headers: { 'X-API-Key': apiKey },
  body: JSON.stringify({
    name: 'Home',
    color: '#4CAF50'
  })
});

// All visits automatically inherit new name
```

---

## Implementation Checklist

### Database
- [ ] Add GIN index on `payload->>'place_id'` for visit queries
- [ ] Add function to update place name + propagate to visits
- [ ] Add cascading update trigger (optional, or handle in API)

### Backend API
- [ ] `PATCH /v1/places/{id}` - Rename place
- [ ] `GET /v1/places` - List all places
- [ ] `GET /v1/places/{id}` - Get place details
- [ ] `DELETE /v1/visits` - Drop all visits
- [ ] Add `place` and `place.visit` to API models

### Place Detection Script
- [ ] DBSCAN clustering function
- [ ] Centroid and radius calculation
- [ ] Place entity generation
- [ ] Batch insert to database

### Visit Detection Script
- [ ] GPS timeline loader
- [ ] Visit detection algorithm
- [ ] Visit entity generation
- [ ] Batch insert with progress bar

### CLI Commands
- [ ] `python place_detector.py cluster --window 2026-01-01,2026-01-31`
- [ ] `python place_detector.py visits --window 2026-01-01,2026-01-31`
- [ ] `python place_detector.py drop-visits --confirm`
- [ ] `python place_detector.py stats`

### Testing
- [ ] Unit tests for haversine distance
- [ ] Test DBSCAN on synthetic data
- [ ] Test visit detection edge cases (overlapping visits, gaps)
- [ ] Test place rename propagation

### Viewer Integration
- [ ] Display places as markers on map
- [ ] Show visit timeline
- [ ] Rename place UI (input field + save button)
- [ ] Color picker for places
- [ ] Visit details tooltip

---

## Dependencies

```txt
# Add to requirements.txt
scikit-learn>=1.3.0  # DBSCAN clustering
numpy>=1.24.0        # Numeric operations
geopy>=2.4.0         # Haversine distance calculations (optional, can use numpy)
```

---

## Performance Considerations

**Clustering**:
- DBSCAN on 1M points: ~30 seconds (depends on eps/min_samples)
- Use spatial indexes if re-clustering frequently
- Consider incremental clustering for new data

**Visit Detection**:
- O(n) scan through sorted GPS timeline
- Process 1M points: ~5-10 seconds
- Can parallelize by place (process each place independently)

**Name Updates**:
- UPDATE queries on indexed `payload->>'place_id'`: milliseconds
- Typical place has 10-1000 visits, updates are fast

**Storage**:
- 100 places × 1KB each = 100KB
- 10,000 visits × 2KB each = 20MB
- Negligible compared to millions of GPS points

---

## Future Enhancements

1. **Incremental Updates**: Only cluster new GPS data since last run
2. **Place Merging**: Detect and merge duplicate places
3. **Place Splitting**: Split places that are actually multiple locations
4. **Visit Patterns**: Detect routines (e.g., "go to work at 9am every weekday")
5. **Place Categories**: Auto-classify places (home, work, restaurant, etc.)
6. **Reverse Geocoding**: Auto-name places using address lookup
7. **Visit Transitions**: Track routes between places
8. **Time-of-Day Heatmaps**: When you typically visit each place

---

## References

- **DBSCAN**: Ester et al., "A Density-Based Algorithm for Discovering Clusters"
- **Haversine Distance**: Great-circle distance for lat/lon coordinates
- **PostGIS**: Spatial queries (if needed for advanced features)
- **Timeline Engine v2**: `doc/timeline-engine-design-v2.md`