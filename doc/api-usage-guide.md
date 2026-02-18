# Daruma API Usage Guide

Complete reference for building client applications that visualize personal timeline data.

**Base URL**: `http://localhost:8000`
**API Version**: v1
**Authentication**: API Key via `X-API-Key` header

---

## Authentication

All endpoints (except `/health` and `/stats`) require an API key:

```http
X-API-Key: your-secret-api-key-here
```

Get your API key from the `.env` file (`API_KEY=...`).

---

## Quick Start

```javascript
const API_BASE = 'http://localhost:8000';
const API_KEY = 'your-key-here';

const headers = {
  'Content-Type': 'application/json',
  'X-API-Key': API_KEY
};

// Query location data for the past week
const response = await fetch(`${API_BASE}/v1/query/time`, {
  method: 'POST',
  headers,
  body: JSON.stringify({
    types: ['location.gps'],
    start: '2026-02-09T00:00:00Z',
    end: '2026-02-16T00:00:00Z',
    limit: 2000
  })
});

const data = await response.json();
console.log(`Found ${data.entities.length} location points`);
```

---

## Entity Model

All entities share this core structure:

```typescript
interface Entity {
  id: string;              // UUID
  type: string;            // e.g., 'location.gps', 'music', 'photo'
  t_start: string;         // ISO 8601 timestamp (UTC)
  t_end: string | null;    // End time for spans, null for instant events
  lat: number | null;      // Latitude (-90 to 90), null if no location
  lon: number | null;      // Longitude (-180 to 180)
  name: string | null;     // Display label
  color: string | null;    // Hex color #RRGGBB for rendering
  render_offset: number | null;  // Vertical offset for timeline view
  source: string | null;   // Source system (e.g., 'arc', 'spotify')
  external_id: string | null;    // Dedup key from source
  loc_source: string | null;     // 'native' or 'inferred'
  payload: object | null;  // Type-specific JSON data
}
```

---

## Core Endpoints

### 1. Query by Time

Get entities within a time window.

**Endpoint**: `POST /v1/query/time`

**Use Cases**:
- Timeline view for a specific date range
- "Show me everything that happened last month"
- Dense location trails with resampling

**Request**:
```typescript
{
  types: string[];          // Entity types to include
  start: string;            // ISO 8601 start time (UTC)
  end: string;              // ISO 8601 end time (UTC)
  limit?: number;           // Max results (default 2000, max 10000)
  order?: 't_start_asc' | 't_start_desc';  // Sort order (default: asc)
  resample?: {              // Optional: uniform time sampling
    method: 'uniform_time';
    n: number;              // Number of samples (1-10000)
  }
}
```

**Response**:
```typescript
{
  entities: Entity[];
}
```

**Examples**:

```javascript
// Get all location points for today
const today = {
  types: ['location.gps'],
  start: '2026-02-16T00:00:00Z',
  end: '2026-02-16T23:59:59Z',
  limit: 5000
};

// Get music listening history for the past week
const music = {
  types: ['music'],
  start: '2026-02-09T00:00:00Z',
  end: '2026-02-16T23:59:59Z',
  order: 't_start_desc'
};

// Get 100 uniformly sampled location points from last month
const sampled = {
  types: ['location.gps'],
  start: '2026-01-01T00:00:00Z',
  end: '2026-01-31T23:59:59Z',
  resample: {
    method: 'uniform_time',
    n: 100
  }
};
```

---

### 2. Query by Bounding Box

Get entities within a geographic area.

**Endpoint**: `POST /v1/query/bbox`

**Use Cases**:
- Map view: "Show me all photos taken in San Francisco"
- Geographic filtering: "What did I do in this neighborhood?"
- Random sampling for map markers

**Request**:
```typescript
{
  types: string[];          // Entity types to include
  bbox: [number, number, number, number];  // [minLon, minLat, maxLon, maxLat]
  time?: {                  // Optional time filter
    start: string;          // ISO 8601
    end: string;
  };
  limit?: number;           // Max results (default 5000, max 10000)
  order?: 't_start_asc' | 't_start_desc' | 'random';  // Sort (default: desc)
}
```

**Response**:
```typescript
{
  entities: Entity[];
}
```

**Examples**:

```javascript
// Get all location points in Los Angeles area
const laArea = {
  types: ['location.gps'],
  bbox: [-118.6682, 33.7037, -118.1553, 34.3373],  // LA bbox
  limit: 10000
};

// Get random sample of 500 locations in SF for heatmap
const sfHeatmap = {
  types: ['location.gps'],
  bbox: [-122.5155, 37.7034, -122.3488, 37.8324],  // SF bbox
  order: 'random',
  limit: 500
};

// Get photos taken in NYC during January 2026
const nycPhotos = {
  types: ['photo'],
  bbox: [-74.0479, 40.6829, -73.9067, 40.8820],  // NYC bbox
  time: {
    start: '2026-01-01T00:00:00Z',
    end: '2026-01-31T23:59:59Z'
  }
};
```

---

### 3. Export All Entities (Streaming)

Stream the entire database (or a filtered subset) as newline-delimited JSON (NDJSON). Designed for bulk data transfer of millions of rows without memory issues on either side.

**Endpoint**: `GET /v1/query/export`

**Use Cases**:
- Full database export / backup
- Syncing all data to another system
- Offline analysis of the complete dataset
- Building local indexes or caches

**Query Parameters**:
| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `types` | `string[]` | No | Entity types to export. Omit for all types. Repeat for multiple: `?types=location.gps&types=music` |
| `order` | `string` | No | `"newest"` (default) or `"oldest"`. Controls timestamp sort order. |

**Response Format**: `application/x-ndjson` (newline-delimited JSON)

The first line is a metadata object with the total count. Each subsequent line is one entity:

```
{"total":4000000}
{"id":"...","type":"location.gps","t_start":"2025-01-01T00:00:00+00:00","lat":34.05,...}
{"id":"...","type":"music","t_start":"2025-01-01T00:05:00+00:00","name":"Karma Police",...}
...
```

**Performance Notes**:
- Uses PostgreSQL server-side cursors — constant memory usage regardless of dataset size
- Bypasses Pydantic serialization for maximum throughput
- Send `Accept-Encoding: gzip` for ~70-80% size reduction (~300-500 MB instead of ~1.5 GB for 4M rows)
- First byte arrives almost instantly; the client can begin parsing before the full response is received

**Examples**:

```javascript
// Stream all entities with progress tracking
async function exportAll(onProgress) {
  const response = await fetch(`${API_BASE}/v1/query/export`, {
    headers: { 'X-API-Key': API_KEY, 'Accept-Encoding': 'gzip' }
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let total = 0;
  let count = 0;
  const entities = [];

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop(); // Keep incomplete last line in buffer

    for (const line of lines) {
      if (!line) continue;
      const obj = JSON.parse(line);

      if ('total' in obj) {
        total = obj.total;
        console.log(`Expecting ${total} entities`);
        continue;
      }

      entities.push(obj);
      count++;
      if (onProgress && count % 10000 === 0) {
        onProgress(count, total);
      }
    }
  }

  return entities;
}

// Export only specific types
const response = await fetch(
  `${API_BASE}/v1/query/export?types=location.gps&types=music`,
  { headers: { 'X-API-Key': API_KEY } }
);
```

```bash
# Export all entities to a file (with gzip)
curl -H "X-API-Key: your-key" -H "Accept-Encoding: gzip" \
  --compressed \
  http://localhost:8000/v1/query/export > export.ndjson

# Export only location data
curl -H "X-API-Key: your-key" --compressed \
  "http://localhost:8000/v1/query/export?types=location.gps" > locations.ndjson

# Count lines (entities) in export
wc -l export.ndjson
```

```python
import httpx

# Stream export with progress
with httpx.stream('GET', f'{API_BASE}/v1/query/export',
                   headers={'X-API-Key': API_KEY}) as r:
    for line in r.iter_lines():
        obj = json.loads(line)
        if 'total' in obj:
            print(f"Exporting {obj['total']} entities...")
            continue
        process_entity(obj)
```

---

### 4. Statistics

Get database statistics and overview.

**Endpoint**: `GET /stats`

**Authentication**: Not required

**Use Cases**:
- Dashboard overview
- Data coverage visualization
- Performance monitoring

**Response**:
```typescript
{
  total_entities: number;
  entities_by_type: Array<{
    type: string;
    count: number;
  }>;
  time_coverage: {
    oldest: string | null;    // ISO 8601
    newest: string | null;
  };
  database: {
    size_mb: number;
    table_size_mb: number;
    index_size_mb: number;
  };
  uptime_seconds: number;
}
```

**Example**:

```javascript
const stats = await fetch('http://localhost:8000/stats');
const data = await stats.json();

console.log(`Total entities: ${data.total_entities}`);
console.log(`Coverage: ${data.time_coverage.oldest} to ${data.time_coverage.newest}`);
console.log(`Types: ${data.entities_by_type.map(t => `${t.type} (${t.count})`).join(', ')}`);
```

---

### 5. Health Check

Check if the API is running.

**Endpoint**: `GET /health`

**Authentication**: Not required

**Response**:
```typescript
{
  status: "ok"
}
```

---

## Common Entity Types

| Type | Description | Has Location | Span vs Instant |
|------|-------------|--------------|-----------------|
| `location.gps` | GPS coordinates from Arc | Native | Instant |
| `music` | Spotify listening history | Inferred | Span (track duration) |
| `photo` | Photo metadata | Native (EXIF) or Inferred | Instant |
| `sleep` | Sleep sessions from Apple Health | Inferred (home) | Span |
| `calendar` | Google Calendar events | Inferred or text | Span |
| `transaction` | Financial transactions | Inferred | Instant |

---

## Client Implementation Patterns

### Pattern 1: Timeline Visualization

Load data for a time range and render chronologically.

```javascript
async function loadTimeline(start, end, types) {
  const response = await fetch(`${API_BASE}/v1/query/time`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      types,
      start,
      end,
      limit: 5000,
      order: 't_start_asc'
    })
  });

  const { entities } = await response.json();

  // Group by date
  const byDate = entities.reduce((acc, entity) => {
    const date = entity.t_start.split('T')[0];
    if (!acc[date]) acc[date] = [];
    acc[date].push(entity);
    return acc;
  }, {});

  return byDate;
}
```

### Pattern 2: Map Visualization

Load data for a geographic viewport.

```javascript
async function loadMapData(bounds, types) {
  const { west, south, east, north } = bounds;

  const response = await fetch(`${API_BASE}/v1/query/bbox`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      types,
      bbox: [west, south, east, north],
      limit: 10000,
      order: 'random'  // Uniform distribution for markers
    })
  });

  const { entities } = await response.json();

  // Convert to map markers
  return entities
    .filter(e => e.lat && e.lon)
    .map(e => ({
      position: [e.lat, e.lon],
      timestamp: e.t_start,
      type: e.type,
      name: e.name,
      color: e.color
    }));
}
```

### Pattern 3: Location Trail

Get movement path with resampling for performance.

```javascript
async function loadLocationTrail(start, end, samples = 1000) {
  const response = await fetch(`${API_BASE}/v1/query/time`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      types: ['location.gps'],
      start,
      end,
      resample: {
        method: 'uniform_time',
        n: samples
      }
    })
  });

  const { entities } = await response.json();

  // Convert to polyline coordinates
  return entities
    .filter(e => e.lat && e.lon)
    .map(e => [e.lat, e.lon]);
}
```

### Pattern 4: Activity Heatmap

Get location density for a region.

```javascript
async function loadHeatmapData(bbox, start, end) {
  const response = await fetch(`${API_BASE}/v1/query/bbox`, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      types: ['location.gps'],
      bbox,
      time: { start, end },
      limit: 10000,
      order: 'random'
    })
  });

  const { entities } = await response.json();

  // Convert to heatmap points with intensity
  return entities
    .filter(e => e.lat && e.lon)
    .map(e => [e.lat, e.lon, 1.0]);  // [lat, lon, intensity]
}
```

---

## Performance Tips

1. **Use appropriate limits**: Start with 2000, increase only if needed
2. **Leverage resampling**: For dense location data, use `uniform_time` resampling
3. **Filter by type**: Only request entity types you'll display
4. **Use bbox for maps**: More efficient than time queries for spatial views
5. **Random sampling**: Use `order: "random"` for map markers to get uniform distribution
6. **Pagination**: For large datasets, query smaller time windows sequentially
7. **Full export**: Use `GET /v1/query/export` for bulk data transfer — streams NDJSON with constant memory
8. **Compression**: The API supports gzip via `Accept-Encoding: gzip` — reduces export payloads by ~70-80%

---

## Error Handling

The API returns standard HTTP status codes:

- `200`: Success
- `400`: Bad request (validation error)
- `401`: Unauthorized (missing or invalid API key)
- `422`: Validation error (check request body)
- `500`: Server error

**Example Error Response**:
```json
{
  "detail": "end must be > start"
}
```

**Robust Client Code**:
```javascript
async function queryWithErrorHandling(endpoint, body) {
  try {
    const response = await fetch(`${API_BASE}${endpoint}`, {
      method: 'POST',
      headers,
      body: JSON.stringify(body)
    });

    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.detail || `HTTP ${response.status}`);
    }

    return await response.json();
  } catch (err) {
    console.error('API Error:', err.message);
    return { entities: [] };  // Graceful fallback
  }
}
```

---

## Type-Specific Payload Examples

### location.gps
```json
{
  "type": "location.gps",
  "t_start": "2026-02-16T14:30:00Z",
  "lat": 34.0522,
  "lon": -118.2437,
  "loc_source": "native",
  "payload": {
    "source_type": "arc_app",
    "original_sample": { /* Arc's raw data */ }
  }
}
```

### music
```json
{
  "type": "music",
  "t_start": "2026-02-16T14:30:00Z",
  "t_end": "2026-02-16T14:33:24Z",
  "name": "Karma Police",
  "color": "#1DB954",
  "loc_source": "inferred",
  "payload": {
    "track": "Karma Police",
    "artist": "Radiohead",
    "album": "OK Computer"
  }
}
```

### photo
```json
{
  "type": "photo",
  "t_start": "2026-02-16T14:30:00Z",
  "lat": 34.0522,
  "lon": -118.2437,
  "name": "IMG_1234.jpg",
  "loc_source": "native",
  "payload": {
    "filename": "IMG_1234.jpg",
    "camera": "iPhone 15 Pro",
    "dimensions": "4032x3024"
  }
}
```

---

## Testing the API

### Using cURL

```bash
# Get stats
curl http://localhost:8000/stats

# Query by time
curl -X POST http://localhost:8000/v1/query/time \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "types": ["location.gps"],
    "start": "2026-02-16T00:00:00Z",
    "end": "2026-02-16T23:59:59Z",
    "limit": 100
  }'

# Query by bbox
curl -X POST http://localhost:8000/v1/query/bbox \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-key" \
  -d '{
    "types": ["location.gps"],
    "bbox": [-118.6682, 33.7037, -118.1553, 34.3373],
    "limit": 100
  }'
```

### Using Python

```python
import requests
from datetime import datetime, timedelta

API_BASE = 'http://localhost:8000'
API_KEY = 'your-key-here'
headers = {'X-API-Key': API_KEY}

# Query last 24 hours
end = datetime.now()
start = end - timedelta(days=1)

response = requests.post(
    f'{API_BASE}/v1/query/time',
    headers=headers,
    json={
        'types': ['location.gps'],
        'start': start.isoformat() + 'Z',
        'end': end.isoformat() + 'Z',
        'limit': 2000
    }
)

data = response.json()
print(f"Found {len(data['entities'])} locations")
```

---

## Interactive API Documentation

Once the server is running, visit:
- **Swagger UI**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

These provide interactive API testing and complete schema documentation.

---

## Next Steps

1. **Start the server**: `python -m app.main` or `uvicorn app.main:app`
2. **Check health**: Visit http://localhost:8000/health
3. **View stats**: Visit http://localhost:8000/stats
4. **Test queries**: Use the examples above or visit `/docs`
5. **Build your viewer**: Use the patterns and examples to implement your visualization

For ingestion and data loading, see the Timeline Engine design document (`doc/timeline-engine-design-v2.md`).
