"""Tests for the entity endpoint."""

from datetime import datetime, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.models import EntityIn
from tests.conftest import make_entity_data


# --- Unit Tests: Model Validation ---


class TestEntityInValidation:
    """Unit tests for EntityIn model validation."""

    def test_valid_entity_minimal(self):
        """Test minimal valid entity with required fields only."""
        entity = EntityIn(
            type="event",
            t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        assert entity.type == "event"
        assert entity.lat is None
        assert entity.lon is None

    def test_valid_entity_with_location(self):
        """Test entity with location."""
        entity = EntityIn(
            type="location.gps",
            t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            lat=34.0522,
            lon=-118.2437,
        )
        assert entity.lat == 34.0522
        assert entity.lon == -118.2437

    def test_valid_entity_with_time_span(self):
        """Test entity with time span."""
        entity = EntityIn(
            type="event",
            t_start=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            t_end=datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc),
        )
        assert entity.t_end > entity.t_start

    def test_invalid_t_end_before_t_start(self):
        """Test that t_end before t_start is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EntityIn(
                type="event",
                t_start=datetime(2024, 1, 1, 14, 0, tzinfo=timezone.utc),
                t_end=datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            )
        assert "t_end must be >= t_start" in str(exc_info.value)

    def test_invalid_lat_only(self):
        """Test that providing only lat is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EntityIn(
                type="location.gps",
                t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                lat=34.0522,
            )
        assert "lat and lon must both be provided" in str(exc_info.value)

    def test_invalid_lon_only(self):
        """Test that providing only lon is rejected."""
        with pytest.raises(ValidationError) as exc_info:
            EntityIn(
                type="location.gps",
                t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                lon=-118.2437,
            )
        assert "lat and lon must both be provided" in str(exc_info.value)

    def test_invalid_lat_out_of_range(self):
        """Test that latitude out of range is rejected."""
        with pytest.raises(ValidationError):
            EntityIn(
                type="location.gps",
                t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                lat=91.0,
                lon=-118.2437,
            )

    def test_invalid_lon_out_of_range(self):
        """Test that longitude out of range is rejected."""
        with pytest.raises(ValidationError):
            EntityIn(
                type="location.gps",
                t_start=datetime(2024, 1, 1, tzinfo=timezone.utc),
                lat=34.0522,
                lon=181.0,
            )


# --- Unit Tests: API Endpoint with Mock DB ---


class TestEntityEndpointUnit:
    """Unit tests for entity endpoint with mocked database."""

    @pytest.mark.asyncio
    async def test_create_entity_returns_id(self, unit_client, mock_pool):
        """Test that creating an entity returns an ID."""
        _, mock_conn = mock_pool
        test_uuid = "12345678-1234-1234-1234-123456789012"
        mock_conn.fetchrow.return_value = {"id": test_uuid, "inserted": True}

        response = await unit_client.post(
            "/v1/entity",
            json=make_entity_data(),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == test_uuid
        assert data["status"] == "inserted"

    @pytest.mark.asyncio
    async def test_upsert_returns_updated_status(self, unit_client, mock_pool):
        """Test that upserting an existing entity returns 'updated' status."""
        _, mock_conn = mock_pool
        test_uuid = "12345678-1234-1234-1234-123456789012"
        mock_conn.fetchrow.return_value = {"id": test_uuid, "inserted": False}

        response = await unit_client.post(
            "/v1/entity",
            json=make_entity_data(source="test.source", external_id="ext123"),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "updated"

    @pytest.mark.asyncio
    async def test_missing_api_key_rejected(self, unit_client):
        """Test that requests without API key are rejected."""
        # Create client without API key header
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post("/v1/entity", json=make_entity_data())

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_api_key_rejected(self, unit_client):
        """Test that requests with invalid API key are rejected."""
        from httpx import ASGITransport, AsyncClient

        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": "wrong-key"},
        ) as client:
            response = await client.post("/v1/entity", json=make_entity_data())

        assert response.status_code == 403


# --- Integration Tests ---


@pytest.mark.integration
class TestEntityEndpointIntegration:
    """Integration tests for entity endpoint with real PostgreSQL."""

    @pytest.mark.asyncio
    async def test_create_entity_insert(self, integration_client):
        """Test creating a new entity."""
        response = await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                lat=34.0522,
                lon=-118.2437,
            ),
        )

        assert response.status_code == 200
        data = response.json()
        assert "id" in data
        assert data["status"] == "inserted"
        # Verify it's a valid UUID
        UUID(data["id"])

    @pytest.mark.asyncio
    async def test_create_entity_without_location(self, integration_client):
        """Test creating an entity without location."""
        response = await integration_client.post(
            "/v1/entity",
            json=make_entity_data(entity_type="event", lat=None, lon=None),
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "inserted"

    @pytest.mark.asyncio
    async def test_upsert_insert_then_update(self, integration_client):
        """Test upsert: first insert, then update."""
        entity_data = make_entity_data(
            entity_type="photo",
            source="test.import",
            external_id="photo123",
            name="Original Name",
        )

        # First insert
        response1 = await integration_client.post("/v1/entity", json=entity_data)
        assert response1.status_code == 200
        assert response1.json()["status"] == "inserted"
        entity_id = response1.json()["id"]

        # Update with same source/external_id
        entity_data["name"] = "Updated Name"
        response2 = await integration_client.post("/v1/entity", json=entity_data)
        assert response2.status_code == 200
        assert response2.json()["status"] == "updated"
        assert response2.json()["id"] == entity_id

    @pytest.mark.asyncio
    async def test_create_entity_with_payload(self, integration_client):
        """Test creating an entity with JSON payload."""
        response = await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="location.gps",
                payload={"accuracy_m": 12.5, "provider": "gps"},
            ),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "inserted"

    @pytest.mark.asyncio
    async def test_create_entity_with_time_span(self, integration_client):
        """Test creating an entity with time span."""
        response = await integration_client.post(
            "/v1/entity",
            json=make_entity_data(
                entity_type="calendar.event",
                t_start=datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc),
                t_end=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
                lat=None,
                lon=None,
                name="Meeting",
            ),
        )

        assert response.status_code == 200
        assert response.json()["status"] == "inserted"
