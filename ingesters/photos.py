"""
Photo EXIF Ingester
Ingests photos by reading EXIF metadata (GPS location and timestamp).

Mirrors the ArcLocationSource pattern: discover → extract → batch upsert.
Uses relative file path as external_id for idempotent upserts.

Dependencies:
    pip install asyncpg tqdm Pillow pillow-heif python-dotenv
"""

import asyncio
import datetime
import json
import os
from pathlib import Path
from typing import Iterator, Dict, Any, Optional
from dataclasses import dataclass, field
import asyncpg
from tqdm import tqdm

# Supported photo extensions
PHOTO_EXTENSIONS = {'.jpg', '.jpeg', '.heic', '.png', '.tiff', '.tif', '.dng', '.arw', '.cr2', '.nef'}

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
    loc_source: Optional[str] = None
    payload: Optional[Dict[str, Any]] = field(default=None)


def _parse_exif_datetime(dt_str: str, tz: datetime.timezone) -> Optional[datetime.datetime]:
    """Parse EXIF DateTimeOriginal format: 'YYYY:MM:DD HH:MM:SS' and attach timezone."""
    try:
        dt = datetime.datetime.strptime(dt_str.strip(), "%Y:%m:%d %H:%M:%S")
        return dt.replace(tzinfo=tz)
    except (ValueError, TypeError):
        return None


def _dms_to_decimal(dms_tuple, ref: str) -> Optional[float]:
    """
    Convert GPS DMS (degrees, minutes, seconds) to decimal degrees.
    Each value in dms_tuple may be a float or a fraction (IFDRational).
    ref is 'N', 'S', 'E', or 'W'.
    """
    try:
        degrees = float(dms_tuple[0])
        minutes = float(dms_tuple[1])
        seconds = float(dms_tuple[2])
        decimal = degrees + minutes / 60.0 + seconds / 3600.0
        if ref in ('S', 'W'):
            decimal = -decimal
        return decimal
    except (TypeError, IndexError, ValueError, ZeroDivisionError):
        return None


def _extract_exif(path: Path) -> Dict[str, Any]:
    """
    Extract EXIF data from a photo using Pillow.
    Returns a dict with parsed fields; empty dict if no EXIF found.
    """
    from PIL import Image
    from PIL.ExifTags import TAGS, GPSTAGS

    result: Dict[str, Any] = {}

    try:
        with Image.open(path) as img:
            raw_exif = img._getexif()
            if not raw_exif:
                return result

            # Decode tag IDs to names
            exif: Dict[str, Any] = {TAGS.get(tid, tid): val for tid, val in raw_exif.items()}

            # --- Timestamp ---
            for field_name in ('DateTimeOriginal', 'DateTimeDigitized', 'DateTime'):
                if field_name in exif:
                    result['datetime_str'] = str(exif[field_name])
                    result['datetime_field'] = field_name
                    break

            # --- GPS ---
            gps_info = exif.get('GPSInfo')
            if gps_info:
                gps: Dict[str, Any] = {GPSTAGS.get(tid, tid): val for tid, val in gps_info.items()}

                lat_dms = gps.get('GPSLatitude')
                lat_ref = gps.get('GPSLatitudeRef', '')
                if lat_dms:
                    lat = _dms_to_decimal(lat_dms, lat_ref)
                    if lat is not None:
                        result['lat'] = lat

                lon_dms = gps.get('GPSLongitude')
                lon_ref = gps.get('GPSLongitudeRef', '')
                if lon_dms:
                    lon = _dms_to_decimal(lon_dms, lon_ref)
                    if lon is not None:
                        result['lon'] = lon

                # GPS timestamp is always UTC
                gps_date = gps.get('GPSDateStamp')
                gps_time = gps.get('GPSTimeStamp')
                if gps_date and gps_time:
                    try:
                        year, month, day = str(gps_date).split(':')
                        h = int(float(gps_time[0]))
                        m = int(float(gps_time[1]))
                        s = int(float(gps_time[2]))
                        result['gps_datetime'] = datetime.datetime(
                            int(year), int(month), int(day), h, m, s,
                            tzinfo=datetime.timezone.utc,
                        )
                    except Exception:
                        pass

                alt = gps.get('GPSAltitude')
                if alt is not None:
                    try:
                        result['altitude'] = float(alt)
                    except (TypeError, ValueError):
                        pass

            # --- Camera metadata for payload ---
            for meta_field in ('Make', 'Model', 'LensModel', 'FocalLength',
                               'ExposureTime', 'FNumber', 'ISOSpeedRatings'):
                if meta_field in exif:
                    result[meta_field] = str(exif[meta_field])

    except Exception as e:
        result['exif_error'] = str(e)

    return result


class PhotoIngester:
    """
    Photo EXIF ingester — walks a directory, reads EXIF, upserts into entities table.
    Follows the same pattern as ArcLocationSource.

    external_id = relative file path (stable dedup key per photo)
    Watermark filters by file mtime for fast incremental runs.
    """

    name = "photos"
    color = "#2196F3"  # Blue for photos

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
            type      = EXCLUDED.type,
            t_start   = EXCLUDED.t_start,
            t_end     = EXCLUDED.t_end,
            lat       = EXCLUDED.lat,
            lon       = EXCLUDED.lon,
            name      = EXCLUDED.name,
            color     = EXCLUDED.color,
            render_offset = EXCLUDED.render_offset,
            loc_source = EXCLUDED.loc_source,
            payload   = EXCLUDED.payload,
            updated_at = now()
        RETURNING id, (xmax = 0) AS inserted;
    """

    def __init__(
        self,
        root_dir: Path,
        db_url: Optional[str] = None,
        local_tz: datetime.timezone = datetime.timezone.utc,
    ):
        self.root_dir = Path(root_dir)
        self.db_url = db_url or self._load_db_url()
        self.local_tz = local_tz  # Applied to DateTimeOriginal when no GPS time

    def _load_db_url(self) -> str:
        if not ENV_FILE.exists():
            raise FileNotFoundError(f".env file not found: {ENV_FILE}")
        try:
            from dotenv import load_dotenv
            load_dotenv(ENV_FILE)
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                return db_url
        except ImportError:
            pass
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("DATABASE_URL="):
                    return line.strip().split("=", 1)[1]
        raise ValueError("DATABASE_URL not found in .env file")

    def _external_id(self, path: Path) -> str:
        """Stable external_id: path relative to root_dir, forward slashes."""
        return str(path.relative_to(self.root_dir)).replace('\\', '/')

    def discover(self, since_mtime: Optional[float] = None) -> Iterator[Path]:
        """
        Recursively yield photo files under root_dir.
        If since_mtime is set, only yield files with mtime >= since_mtime.
        """
        for path in self.root_dir.rglob('*'):
            if not path.is_file():
                continue
            if path.suffix.lower() not in PHOTO_EXTENSIONS:
                continue
            if since_mtime is not None and path.stat().st_mtime < since_mtime:
                continue
            yield path

    def extract(self, path: Path) -> Optional[Entity]:
        """
        Read EXIF from a photo and return a normalized Entity.
        Returns None only if the file cannot be read at all.

        Timestamp priority:
          1. GPS timestamp (UTC, most accurate)
          2. DateTimeOriginal interpreted with --timezone
          3. File modification time (fallback)
        """
        suffix = path.suffix.lower()
        if suffix == '.heic':
            try:
                import pillow_heif
                pillow_heif.register_heif_opener()
            except ImportError:
                print(f"  Warning: pillow-heif not installed, skipping {path.name}")
                return None

        exif = _extract_exif(path)

        # --- Timestamp ---
        timestamp: Optional[datetime.datetime] = None
        timestamp_source: str = 'file_mtime'

        if 'gps_datetime' in exif:
            timestamp = exif['gps_datetime']
            timestamp_source = 'gps_utc'
        elif 'datetime_str' in exif:
            timestamp = _parse_exif_datetime(exif['datetime_str'], self.local_tz)
            if timestamp:
                timestamp_source = f"exif_{exif.get('datetime_field', 'DateTimeOriginal')}"

        if timestamp is None:
            mtime = path.stat().st_mtime
            timestamp = datetime.datetime.fromtimestamp(mtime, tz=datetime.timezone.utc)
            timestamp_source = 'file_mtime'

        # --- Location ---
        lat = exif.get('lat')
        lon = exif.get('lon')
        loc_source = 'native' if (lat is not None and lon is not None) else None

        # --- Payload ---
        payload: Dict[str, Any] = {
            'filename': path.name,
            'timestamp_source': timestamp_source,
        }
        for meta_field in ('Make', 'Model', 'LensModel', 'FocalLength',
                           'ExposureTime', 'FNumber', 'ISOSpeedRatings'):
            if meta_field in exif:
                payload[meta_field] = exif[meta_field]
        if 'altitude' in exif:
            payload['altitude'] = exif['altitude']
        if 'exif_error' in exif:
            payload['exif_error'] = exif['exif_error']

        return Entity(
            type='photo',
            t_start=timestamp,
            t_end=None,
            lat=lat,
            lon=lon,
            name=path.stem,
            color=self.color,
            external_id=self._external_id(path),
            loc_source=loc_source,
            payload=payload,
        )

    async def get_watermark(self, conn: asyncpg.Connection) -> Optional[datetime.datetime]:
        row = await conn.fetchrow(
            "SELECT last_run FROM source_state WHERE source = $1",
            self.name,
        )
        return row['last_run'] if row else None

    async def set_watermark(
        self,
        conn: asyncpg.Connection,
        timestamp: datetime.datetime,
        count: int,
    ):
        await conn.execute(
            """
            INSERT INTO source_state (source, last_run, last_count)
            VALUES ($1, $2, $3)
            ON CONFLICT (source) DO UPDATE SET
                last_run   = EXCLUDED.last_run,
                last_count = EXCLUDED.last_count,
                updated_at = now()
            """,
            self.name,
            timestamp,
            count,
        )
        print(f"Watermark updated: {timestamp.isoformat()} ({count} photos)")

    async def run(self, use_watermark: bool = True):
        conn = await asyncpg.connect(self.db_url)
        try:
            since_mtime: Optional[float] = None

            if use_watermark:
                wm = await self.get_watermark(conn)
                if wm is not None:
                    since_mtime = wm.timestamp()
                    print(f"Watermark: {wm.isoformat()} — skipping files older than this")
                else:
                    print("No watermark found — processing all photos")
            else:
                print("Watermark disabled — processing all photos")

            print(f"\n=== Photo EXIF Ingester ===")
            print(f"Source:   {self.name}")
            print(f"Root dir: {self.root_dir}")

            start_time = datetime.datetime.now(datetime.timezone.utc)

            print("\nDiscovering photos...")
            photo_paths = list(self.discover(since_mtime=since_mtime))
            print(f"Found {len(photo_paths)} photo file(s) to process")

            if not photo_paths:
                print("Nothing to do.")
                return

            entities: list[Entity] = []
            errors = 0

            print("Reading EXIF data...")
            for path in tqdm(photo_paths, desc="Reading EXIF", unit="photo"):
                try:
                    entity = self.extract(path)
                    if entity:
                        entities.append(entity)
                except Exception as e:
                    print(f"\nError processing {path}: {e}")
                    errors += 1

            print(f"Extracted {len(entities)} entities ({errors} read errors)")

            if not entities:
                print("No entities to insert.")
                return

            inserted_count, updated_count = await self._batch_insert(conn, entities)

            if use_watermark:
                await self.set_watermark(conn, start_time, len(entities))

            print(f"\n=== Ingestion Complete ===")
            print(f"Inserted: {inserted_count}")
            print(f"Updated:  {updated_count}")
            print(f"Errors:   {errors}")
            print(f"Total:    {inserted_count + updated_count}")

        finally:
            await conn.close()

    async def _batch_insert(
        self,
        conn: asyncpg.Connection,
        entities: list[Entity],
    ) -> tuple[int, int]:
        inserted_count = 0
        updated_count = 0

        print("\nInserting into database...")
        async with conn.transaction():
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
                        0.0,          # render_offset
                        self.name,    # source
                        entity.external_id,
                        entity.loc_source,
                        json.dumps(entity.payload) if entity.payload else None,
                    )
                    if result['inserted']:
                        inserted_count += 1
                        action = "INSERT"
                    else:
                        updated_count += 1
                        action = "UPDATE"

                    loc_str = (
                        f"  lat={entity.lat:.5f} lon={entity.lon:.5f}"
                        if entity.lat is not None
                        else "  no GPS"
                    )
                    tqdm.write(
                        f"[{action}] {entity.external_id}"
                        f"  t={entity.t_start.isoformat()}"
                        f"{loc_str}"
                    )
                except Exception as e:
                    tqdm.write(f"[ERROR] {entity.external_id}: {e}")

        return inserted_count, updated_count


def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Photo EXIF Ingester — reads GPS and timestamp from photos, inserts into Daruma.",
    )
    parser.add_argument(
        '--root-dir', type=str, required=True,
        help='Root directory to scan for photos (scanned recursively)',
    )
    parser.add_argument(
        '--db-url', type=str,
        help='PostgreSQL connection URL (overrides .env)',
    )
    parser.add_argument(
        '--no-watermark', action='store_true',
        help='Process all photos regardless of last run time',
    )
    parser.add_argument(
        '--timezone', type=str, default='+00:00',
        help='UTC offset for DateTimeOriginal when GPS time is unavailable (e.g. -05:00, +09:00). Default: +00:00',
    )
    args = parser.parse_args()

    # Parse timezone offset
    try:
        sign = 1 if args.timezone.startswith('+') else -1
        tz_str = args.timezone.lstrip('+-')
        h, m = (int(x) for x in tz_str.split(':'))
        local_tz = datetime.timezone(datetime.timedelta(hours=sign * h, minutes=sign * m))
    except Exception:
        print(f"Warning: could not parse timezone '{args.timezone}', using UTC")
        local_tz = datetime.timezone.utc

    kwargs: Dict[str, Any] = {
        'root_dir': Path(args.root_dir),
        'local_tz': local_tz,
    }
    if args.db_url:
        kwargs['db_url'] = args.db_url

    ingester = PhotoIngester(**kwargs)
    asyncio.run(ingester.run(use_watermark=not args.no_watermark))


if __name__ == '__main__':
    main()
