"""
Arc Location Data Ingester
First Timeline Engine source plugin - ingests GPS location data from Arc app exports.

Arc exports daily JSON files with timeline items and location samples.
This ingester writes directly to PostgreSQL using asyncpg (no HTTP overhead).
"""

import asyncio
import datetime
import gzip
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Iterator, Dict, Any, Optional
from dataclasses import dataclass
import asyncpg
from tqdm import tqdm

# Configuration
ROOT_DIR = Path("D:/iCloudDrive/iCloud~com~bigpaua~LearnerCoacher/Export/JSON/Daily")
ENV_FILE = Path(__file__).parent.parent / ".env"


@dataclass
class Entity:
    """Normalized entity ready for database insertion."""
    type: str
    t_start: datetime.datetime
    t_end: Optional[datetime.datetime] = None
    lat: Optional[float] = None
    lon: Optional[float] = None
    name: Optional[str] = None
    color: Optional[str] = None
    external_id: Optional[str] = None
    loc_source: str = "native"
    payload: Optional[Dict[str, Any]] = None


class ArcLocationSource:
    """
    Arc location data source - GPS samples from Arc app daily exports.

    Writes directly to PostgreSQL using asyncpg for maximum performance.
    Arc exports compressed JSON files containing timeline items with location samples.
    """

    name = "arc"
    schedule = "0 * * * *"  # Hourly as per design doc

    # SQL for upserting entities (from design doc)
    UPSERT_SQL = """
        INSERT INTO entities (
            type, t_start, t_end, lat, lon,
            name, color, render_offset,
            source, external_id, loc_source,
            payload
        ) VALUES (
            $1, $2, $3, $4, $5,
            $6, $7, $8,
            $9, $10, $11,
            $12::jsonb
        )
        ON CONFLICT (source, external_id)
        WHERE source IS NOT NULL AND external_id IS NOT NULL
        DO UPDATE SET
            type = EXCLUDED.type,
            t_start = EXCLUDED.t_start,
            t_end = EXCLUDED.t_end,
            lat = EXCLUDED.lat,
            lon = EXCLUDED.lon,
            name = EXCLUDED.name,
            color = EXCLUDED.color,
            render_offset = EXCLUDED.render_offset,
            loc_source = EXCLUDED.loc_source,
            payload = EXCLUDED.payload,
            updated_at = now()
        RETURNING id, (xmax = 0) AS inserted;
    """

    def __init__(self, root_dir: Path = ROOT_DIR, db_url: Optional[str] = None):
        self.root_dir = Path(root_dir)
        self.db_url = db_url or self._load_db_url()

    def _load_db_url(self) -> str:
        """Load database URL from .env file"""
        if not ENV_FILE.exists():
            raise FileNotFoundError(f".env file not found: {ENV_FILE}")

        # Try python-dotenv first
        try:
            from dotenv import load_dotenv
            load_dotenv(ENV_FILE)
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                return db_url
        except ImportError:
            pass

        # Fallback to manual parsing
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.strip().split("=", 1)[1]

        raise ValueError("DATABASE_URL not found in .env file")

    def has_native_location(self) -> bool:
        """Arc provides native GPS coordinates."""
        return True

    def discover(self, since: datetime.datetime) -> Iterator[Dict[str, Any]]:
        """
        Yield location samples from Arc JSON files that are newer than `since`.

        Arc exports are organized as daily compressed JSON files (YYYY-MM-DD.json.gz).
        Each file contains timeline items with location samples.

        Args:
            since: Only yield samples with timestamps after this datetime

        Yields:
            Raw location sample dicts from Arc JSON
        """
        # Find all compressed Arc export files
        compressed_files = sorted(self.root_dir.glob("*.json.gz"))

        if not compressed_files:
            print(f"Warning: No Arc JSON files found in {self.root_dir}")
            return

        print(f"Found {len(compressed_files)} Arc export files")
        sample_count = 0

        for compressed_file in compressed_files:
            try:
                # Workaround for iCloud files on Windows: they have special attributes
                # that prevent Python from opening them. Copy to temp first.
                with tempfile.NamedTemporaryFile(delete=False, suffix='.json.gz') as temp_file:
                    temp_path = temp_file.name
                try:
                    shutil.copy2(compressed_file, temp_path)

                    with gzip.open(temp_path, 'rb') as f:
                        file_content = f.read()
                        raw_content = file_content.decode('utf-8')
                        data = json.loads(raw_content)
                finally:
                    # Clean up temp file
                    try:
                        os.unlink(temp_path)
                    except:
                        pass

                # Extract timeline items
                timeline_items = data.get('timelineItems', [])

                # Extract samples from all timeline items in this file
                for item in timeline_items:
                    samples = item.get('samples', [])

                    for sample in samples:
                        location = sample.get('location')

                        # Skip samples without location data
                        if location is None:
                            continue

                        # Parse the timestamp
                        try:
                            timestamp_str = location.get('timestamp')
                            if not timestamp_str:
                                continue

                            # Parse ISO 8601 timestamp
                            timestamp = datetime.datetime.fromisoformat(
                                timestamp_str.replace('Z', '+00:00')
                            )

                            # Only yield if newer than watermark
                            if timestamp > since:
                                sample_count += 1
                                yield {
                                    'timestamp': timestamp_str,
                                    'latitude': location.get('latitude'),
                                    'longitude': location.get('longitude'),
                                    'sample': sample  # Keep full sample for payload
                                }

                        except (ValueError, TypeError) as e:
                            print(f"Error parsing timestamp in {compressed_file}: {e}")
                            continue

            except Exception as e:
                print(f"Error processing {compressed_file}: {e}")
                continue

        print(f"Discovered {sample_count} samples newer than {since}")

    def extract(self, raw: Dict[str, Any]) -> Entity:
        """
        Transform a raw Arc location sample into a normalized Entity.

        Args:
            raw: Dict with 'timestamp', 'latitude', 'longitude', and 'sample' keys

        Returns:
            Entity ready for database insertion
        """
        timestamp_str = raw['timestamp']

        # Parse timestamp
        timestamp = datetime.datetime.fromisoformat(
            timestamp_str.replace('Z', '+00:00')
        )

        return Entity(
            type='location.gps',
            t_start=timestamp,
            t_end=None,  # Instantaneous GPS sample
            lat=raw['latitude'],
            lon=raw['longitude'],
            name=None,  # GPS samples don't have names
            color='#4CAF50',  # Green for location data
            external_id=timestamp_str,  # Use timestamp as dedup key
            loc_source='native',  # Arc provides native GPS
            payload={
                'source_type': 'arc_app',
                'original_sample': raw.get('sample', {})
            }
        )

    async def get_watermark(self, conn: asyncpg.Connection) -> Optional[datetime.datetime]:
        """
        Get the last successful run timestamp from source_state table.

        Args:
            conn: Database connection

        Returns:
            Last run timestamp, or None if this is the first run
        """
        row = await conn.fetchrow(
            "SELECT last_run FROM source_state WHERE source = $1",
            self.name
        )

        if row:
            return row['last_run']
        return None

    async def set_watermark(
        self,
        conn: asyncpg.Connection,
        timestamp: datetime.datetime,
        count: int
    ):
        """
        Update the watermark after successful ingestion.

        Args:
            conn: Database connection
            timestamp: Timestamp to set as last_run
            count: Number of entities processed
        """
        await conn.execute(
            """
            INSERT INTO source_state (source, last_run, last_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (source) DO UPDATE SET
                last_run = EXCLUDED.last_run,
                last_count = EXCLUDED.last_count,
                updated_at = now()
            """,
            self.name,
            timestamp,
            count
        )
        print(f"Watermark updated: {timestamp.isoformat()} ({count} entities)")

    async def run(
        self,
        since: Optional[datetime.datetime] = None,
        use_watermark: bool = True
    ):
        """
        Run the ingestion: discover samples since watermark and insert to database.

        Args:
            since: Start date for ingestion. If None and use_watermark=True, uses stored watermark.
                   If None and use_watermark=False, uses epoch (imports all data).
            use_watermark: Whether to use the stored watermark for incremental sync
        """
        # Connect to database
        conn = await asyncpg.connect(self.db_url)

        try:
            # Determine starting timestamp
            if since is None:
                if use_watermark:
                    since = await self.get_watermark(conn)
                    if since is None:
                        # First run - start from a reasonable date
                        since = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)
                        print("No watermark found - this is the first run")
                else:
                    since = datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)

            print(f"\n=== Arc Location Ingester ===")
            print(f"Source: {self.name}")
            print(f"Discovering samples since: {since}")
            print(f"Data directory: {self.root_dir}")

            # Collect entities to insert
            entities = []
            start_time = datetime.datetime.now(datetime.timezone.utc)

            print("\nDiscovering entities...")
            for raw_sample in self.discover(since):
                try:
                    entity = self.extract(raw_sample)
                    entities.append(entity)
                except Exception as e:
                    print(f"Error extracting entity: {e}")
                    continue

            print(f"Found {len(entities)} new location samples")

            if not entities:
                print("No new samples to insert")
                return

            # Insert entities with progress bar
            inserted_count, updated_count = await self._batch_insert(conn, entities)

            # Update watermark after successful ingestion
            if use_watermark:
                await self.set_watermark(conn, start_time, len(entities))

            print(f"\n=== Ingestion Complete ===")
            print(f"Inserted: {inserted_count}")
            print(f"Updated: {updated_count}")
            print(f"Total processed: {inserted_count + updated_count}")

        finally:
            await conn.close()

    async def _batch_insert(
        self,
        conn: asyncpg.Connection,
        entities: list[Entity]
    ) -> tuple[int, int]:
        """
        Insert entities using PostgreSQL batch operations.

        Args:
            conn: Database connection
            entities: List of Entity objects to insert

        Returns:
            Tuple of (inserted_count, updated_count)
        """
        inserted_count = 0
        updated_count = 0

        print("\nInserting entities...")

        # Use transaction for atomicity
        async with conn.transaction():
            # Insert with progress bar
            for entity in tqdm(entities, desc="Inserting", unit="entities"):
                try:
                    result = await conn.fetchrow(
                        self.UPSERT_SQL,
                        entity.type,
                        entity.t_start,
                        entity.t_end,
                        entity.lat,
                        entity.lon,
                        entity.name,
                        entity.color,
                        0.0,  # render_offset
                        self.name,  # source
                        entity.external_id,
                        entity.loc_source,
                        json.dumps(entity.payload) if entity.payload else None
                    )

                    if result['inserted']:
                        inserted_count += 1
                    else:
                        updated_count += 1

                except Exception as e:
                    print(f"\nError inserting entity: {e}")
                    continue

        return inserted_count, updated_count


def main():
    """Run the Arc location ingester."""
    import argparse

    parser = argparse.ArgumentParser(description='Arc Location Data Ingester (Direct PostgreSQL)')
    parser.add_argument('--since', type=str, help='ISO timestamp to start from (e.g., 2024-01-01T00:00:00Z)')
    parser.add_argument('--root-dir', type=str, help='Root directory for Arc JSON files')
    parser.add_argument('--db-url', type=str, help='PostgreSQL connection URL')
    parser.add_argument('--no-watermark', action='store_true', help='Disable watermark-based incremental sync')

    args = parser.parse_args()

    # Parse since timestamp
    since = None
    if args.since:
        since = datetime.datetime.fromisoformat(args.since.replace('Z', '+00:00'))

    # Initialize source
    kwargs = {}
    if args.root_dir:
        kwargs['root_dir'] = Path(args.root_dir)
    if args.db_url:
        kwargs['db_url'] = args.db_url

    source = ArcLocationSource(**kwargs)

    # Run ingestion (async)
    asyncio.run(source.run(since=since, use_watermark=not args.no_watermark))


if __name__ == '__main__':
    main()
