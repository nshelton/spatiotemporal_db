"""Tests for the bbox query endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import BBoxQueryRequest
from tests.conftest import make_entity_data


# --- Unit Tests: Model Validation ---


class TestBBoxQueryRequestValidation:
    """Unit tests for BBoxQueryRequest model validation."""

    def test_valid_bbox_query(self):
        """Test valid bbox query request."""
        query = BBoxQueryRequest(
            types=["location.gps"],
            bbox=[-118.55, 33.90, -118.15, 34.10],
        )
        assert query.limit == 5000  # default
        assert query.order == "t_start_desc"  # default

    def test_valid_bbox_query_with_time(self):
        """Test valid bbox query with time window."""
        query = BBoxQueryRequest(
            types=["location.gps"],
            bbox=[-118.55, 33.90, -118.15, 34.10],
            time={
                "start": datetime(2024, 1, 1, tzinfo=timezone.utc),
                "end": datetime(2024, 12, 31, tzinfo=timezone.utc),
            },
        )
        assert query.time is not None
        assert query.time.start.year == 2024

    def test_invalid_empty_types(self):
        """Test that empty types list is rejected."""
        with pytest.raises(ValidationError):
            BBoxQueryRequest(
                types=[],
                bbox=[-118.55, 33.90, -118.15, 34.10],
            )

    def test_invalid_bbox_wrong_length(self):
        """Test that bbox with wrong length is rejected."""
        with pytest.raises(ValidationError):
            BBoxQueryRequest(
                types=["location.gps"],
                bbox=[-118.55, 33.90, -118.15],  # only 3 values
            )

    def test_invalid_bbox_min_lon_gte_max_lon(self):
        """Test that minLon >= maxLon is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            BBoxQueryRequest(
                types=["location.gps"],
                bbox=[-118.15, 33.90, -118.55, 34.10],  # min > max
            )
        assert "minLon must be < maxLon" in str(exc_info.value)

    def test_invalid_bbox_min_lat_gte_max_lat(self):
        """Test that minLat >= maxLat is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            BBoxQueryRequest(
                types=["location.gps"],
                bbox=[-118.55, 34.10, -118.15, 33.90],  # min > max
            )
        assert "minLat must be < maxLat" in str(exc_info.value)

    def test_invalid_bbox_lon_out_of_range(self):
        """Test that longitude out of range is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            BBoxQueryRequest(
                types=["location.gps"],
                bbox=[-200.0, 33.90, -118.15, 34.10],  # -200 is invalid
            )
        assert "longitude must be between" in str(exc_info.value)

    def test_invalid_bbox_lat_out_of_range(self):
        """Test that latitude out of range is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            BBoxQueryRequest(
                types=["location.gps"],
                bbox=[-118.55, 100.0, -118.15, 34.10],  # 100 is invalid
            )
        assert "latitude must be between" in str(exc_info.value)


# --- Unit Tests: API Endpoint with Mock DB ---


class TestBBoxQueryEndpointUnit:
    """Unit tests for bbox query endpoint with mocked database."""

    @pytest.mark.asyncio
    async def test_bbox_query_returns_entities(self, unit_client, mock_pool):
        """Test that bbox query returns entities."""
        _, mock_conn = mock_pool
        mock_conn.fetch.return_value = [
            {
                "id": "12345678-1234-1234-1234-123456789012",
                "type": "location.gps",
                "t_start": datetime(2024, 6, 1, tzinfo=timezone.utc),
                "t_end": None,
                "lat": 34.0522,
                "lon": -118.2437,
                "name": None,
                "color": None,
                "render_offset": None,
                "source": None,
                "external_id": None,
                "payload": None,
            }
        ]

        response = await unit_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.55, 33.90, -118.15, 34.10],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "entities" in data
        assert len(data["entities"]) == 1

    @pytest.mark.asyncio
    async def test_bbox_query_empty_result(self, unit_client, mock_pool):
        """Test bbox query with no matching entities."""
        _, mock_conn = mock_pool
        mock_conn.fetch.return_value = []

        response = await unit_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.55, 33.90, -118.15, 34.10],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["entities"] == []


# --- Integration Tests ---


@pytest.mark.integration
class TestBBoxQueryIntegration:
    """Integration tests for bbox query endpoint with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_bbox_query_finds_entities_in_bounds(self, integration_client):
        """Test that bbox query finds entities within the bounding box."""
        # Create entities at different locations
        # Los Angeles area
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=34.0522,
                lon=-118.2437,
                name="LA Downtown",
            ),
        )
        # Santa Monica
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=34.0195,
                lon=-118.4912,
                name="Santa Monica",
            ),
        )
        # New York (outside LA bbox)
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=40.7128,
                lon=-74.0060,
                name="NYC",
            ),
        )

        # Query for LA area only
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],  # LA area
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 2
        names = [e["name"] for e in data["entities"]]
        assert "LA Downtown" in names
        assert "Santa Monica" in names
        assert "NYC" not in names

    @pytest.mark.asyncio
    async def test_bbox_query_filters_by_type(self, integration_client):
        """Test that bbox query filters by entity type."""
        # Create GPS and photo at same location
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(entity_type="location.gps", lat=34.0522, lon=-118.2437),
        )
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(entity_type="photo", lat=34.0522, lon=-118.2437),
        )

        # Query for GPS only
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["type"] == "location.gps"

    @pytest.mark.asyncio
    async def test_bbox_query_with_time_filter(self, integration_client):
        """Test bbox query with time window filter."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create entities at same location but different times
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=34.0522,
                lon=-118.2437,
                t_start=base_time,
                name="June entity",
            ),
        )
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=34.0522,
                lon=-118.2437,
                t_start=base_time + timedelta(days=60),  # August
                name="August entity",
            ),
        )

        # Query with June time filter
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],
                "time": {
                    "start": "2024-06-01T00:00:00Z",
                    "end": "2024-06-30T00:00:00Z",
                },
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["name"] == "June entity"

    @pytest.mark.asyncio
    async def test_bbox_query_respects_limit(self, integration_client):
        """Test that bbox query respects the limit parameter."""
        # Create 10 entities in the same area
        for i in range(10):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    lat=34.0522 + (i * 0.001),  # Slightly different locations
                    lon=-118.2437,
                    t_start=datetime(2024, 6, 15, i, 0, tzinfo=timezone.utc),
                ),
            )

        # Query with limit of 5
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],
                "limit": 5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 5

    @pytest.mark.asyncio
    async def test_bbox_query_ordering(self, integration_client):
        """Test that bbox query respects ordering."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create entities in order
        for i in range(3):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    lat=34.0522,
                    lon=-118.2437,
                    t_start=base_time + timedelta(hours=i),
                ),
            )

        # Query with ascending order
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["location.gps"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],
                "order": "t_start_asc",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 3
        # Check ascending order
        times = [e["t_start"] for e in data["entities"]]
        assert times == sorted(times)

    @pytest.mark.asyncio
    async def test_bbox_query_excludes_entities_without_location(self, integration_client):
        """Test that bbox query excludes entities without location."""
        # Create entity with location
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="event",
                lat=34.0522,
                lon=-118.2437,
                name="Event with location",
            ),
        )
        # Create entity without location
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="event",
                lat=None,
                lon=None,
                name="Event without location",
            ),
        )

        # Query for events in the area
        response = await integration_client.post(
            "/v1/query/bbox",
            json={
                "types": ["event"],
                "bbox": [-118.6, 33.9, -118.1, 34.2],
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["name"] == "Event with location"
