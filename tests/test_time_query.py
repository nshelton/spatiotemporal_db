"""Tests for the time query endpoint."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models import ResampleConfig, TimeQueryRequest
from tests.conftest import make_entity_data


# --- Unit Tests: Model Validation ---


class TestTimeQueryRequestValidation:
    """Unit tests for TimeQueryRequest model validation."""

    def test_valid_time_query(self):
        """Test valid time query request."""
        query = TimeQueryRequest(
            types=["location.gps"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 12, 31, tzinfo=timezone.utc),
        )
        assert query.limit == 2000  # default
        assert query.order == "t_start_asc"  # default

    def test_valid_time_query_with_resample(self):
        """Test valid time query with resampling."""
        query = TimeQueryRequest(
            types=["location.gps"],
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            resample=ResampleConfig(method="uniform_time", n=500),
        )
        assert query.resample.method == "uniform_time"
        assert query.resample.n == 500

    def test_invalid_empty_types(self):
        """Test that empty types list is rejected."""
        with pytest.raises(ValidationError):
            TimeQueryRequest(
                types=[],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
            )

    def test_invalid_end_before_start(self):
        """Test that end <= start is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            TimeQueryRequest(
                types=["event"],
                start=datetime(2024, 12, 31, tzinfo=timezone.utc),
                end=datetime(2024, 1, 1, tzinfo=timezone.utc),
            )
        assert "end must be > start" in str(exc_info.value)

    def test_invalid_resample_missing_n(self):
        """Test that uniform_time resample without n is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            ResampleConfig(method="uniform_time")
        assert "n is required" in str(exc_info.value)

    def test_invalid_limit_too_high(self):
        """Test that limit > 10000 is rejected."""
        with pytest.raises(ValidationError):
            TimeQueryRequest(
                types=["event"],
                start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                end=datetime(2024, 12, 31, tzinfo=timezone.utc),
                limit=20000,
            )


# --- Unit Tests: API Endpoint with Mock DB ---


class TestTimeQueryEndpointUnit:
    """Unit tests for time query endpoint with mocked database."""

    @pytest.mark.asyncio
    async def test_time_query_returns_entities(self, unit_client, mock_pool):
        """Test that time query returns entities."""
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
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-12-31T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert "entities" in data
        assert len(data["entities"]) == 1

    @pytest.mark.asyncio
    async def test_time_query_empty_result(self, unit_client, mock_pool):
        """Test time query with no matching entities."""
        _, mock_conn = mock_pool
        mock_conn.fetch.return_value = []

        response = await unit_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-01-01T00:00:00Z",
                "end": "2024-12-31T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["entities"] == []


# --- Integration Tests ---


@pytest.mark.integration
class TestTimeQueryIntegration:
    """Integration tests for time query endpoint with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_time_query_finds_entities_in_range(self, integration_client):
        """Test that time query finds entities within the time range."""
        # Create entities at different times
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        for i in range(5):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    t_start=base_time + timedelta(days=i),
                ),
            )

        # Query for the middle of the range
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-06-16T00:00:00Z",
                "end": "2024-06-18T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should find entities on June 16 and 17
        assert len(data["entities"]) == 2

    @pytest.mark.asyncio
    async def test_time_query_filters_by_type(self, integration_client):
        """Test that time query filters by entity type."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create GPS and event entities
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(entity_type="location.gps", t_start=base_time),
        )
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="event", t_start=base_time, lat=None, lon=None
            ),
        )

        # Query for GPS only
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-30T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["type"] == "location.gps"

    @pytest.mark.asyncio
    async def test_time_query_multiple_types(self, integration_client):
        """Test querying for multiple types."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create different entity types
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(entity_type="location.gps", t_start=base_time),
        )
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="photo", t_start=base_time + timedelta(hours=1)
            ),
        )
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="event",
                t_start=base_time + timedelta(hours=2),
                lat=None,
                lon=None,
            ),
        )

        # Query for GPS and photo
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps", "photo"],
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-30T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 2

    @pytest.mark.asyncio
    async def test_time_query_respects_limit(self, integration_client):
        """Test that time query respects the limit parameter."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create 10 entities
        for i in range(10):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    t_start=base_time + timedelta(minutes=i),
                ),
            )

        # Query with limit of 5
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-30T00:00:00Z",
                "limit": 5,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 5

    @pytest.mark.asyncio
    async def test_time_query_ordering(self, integration_client):
        """Test that time query respects ordering."""
        base_time = datetime(2024, 6, 15, 12, 0, tzinfo=timezone.utc)

        # Create entities in order
        for i in range(3):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    t_start=base_time + timedelta(hours=i),
                ),
            )

        # Query with descending order
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-06-01T00:00:00Z",
                "end": "2024-06-30T00:00:00Z",
                "order": "t_start_desc",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 3
        # Check descending order
        times = [e["t_start"] for e in data["entities"]]
        assert times == sorted(times, reverse=True)

    @pytest.mark.asyncio
    async def test_time_query_finds_spanning_entities(self, integration_client):
        """Test that time query finds entities that span the query window."""
        # Create an event that spans multiple days
        await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="calendar.event",
                t_start=datetime(2024, 6, 10, tzinfo=timezone.utc),
                t_end=datetime(2024, 6, 20, tzinfo=timezone.utc),
                lat=None,
                lon=None,
                name="Multi-day event",
            ),
        )

        # Query for a window in the middle of the span
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["calendar.event"],
                "start": "2024-06-14T00:00:00Z",
                "end": "2024-06-16T00:00:00Z",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["entities"]) == 1
        assert data["entities"][0]["name"] == "Multi-day event"

    @pytest.mark.asyncio
    async def test_time_query_with_resample(self, integration_client):
        """Test time query with uniform_time resampling."""
        base_time = datetime(2024, 6, 15, 0, 0, tzinfo=timezone.utc)

        # Create 100 entities spread over 10 hours
        for i in range(100):
            await integration_client.post(
                "/v1/entity",
                json=make_entity_data(
                    entity_type="location.gps",
                    t_start=base_time + timedelta(minutes=i * 6),  # every 6 min
                ),
            )

        # Query with resampling to 10 points
        response = await integration_client.post(
            "/v1/query/time",
            json={
                "types": ["location.gps"],
                "start": "2024-06-15T00:00:00Z",
                "end": "2024-06-15T10:00:00Z",
                "resample": {"method": "uniform_time", "n": 10},
            },
        )

        assert response.status_code == 200
        data = response.json()
        # Should return approximately 10 entities (some bins might be empty)
        assert len(data["entities"]) <= 10
