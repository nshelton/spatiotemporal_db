"""
Photo serving routes.

GET /v1/photo/{uuid}        — stream original file
GET /v1/photo/{uuid}/thumb  — JPEG thumbnail, lazily generated and cached to disk

Auth: X-API-Key header OR ?api_key= query param.
The query param form lets <img src="/v1/photo/{uuid}/thumb?api_key=KEY"> work
directly in HTML/canvas/WebGL without JS fetch + blob URL tricks.

Thumbnail cache lives at THUMB_CACHE_DIR (default: PHOTO_ROOT/.daruma_thumbs/).
Cache key: {uuid}.jpg — flat folder, O(1) lookup, no path traversal risk.
Thumbnails generated in a ThreadPoolExecutor so Pillow doesn't block the event loop.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from app.config import settings
from app.db import get_connection

router = APIRouter(prefix="/v1/photo", tags=["photo"])

_thumb_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="thumb")

_MIME = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".heic": "image/heic",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
    ".dng":  "image/x-adobe-dng",
    ".arw":  "image/x-sony-arw",
    ".cr2":  "image/x-canon-cr2",
    ".nef":  "image/x-nikon-nef",
}


def _check_auth(request: Request, api_key: str | None) -> None:
    """Accept API key from X-API-Key header OR ?api_key= query param."""
    key = request.headers.get("X-API-Key") or api_key
    if not key:
        raise HTTPException(status_code=401, detail="Missing API key")
    if key != settings.api_key:
        raise HTTPException(status_code=403, detail="Invalid API key")


def _photo_root() -> Path:
    if settings.photo_root is None:
        raise HTTPException(status_code=503, detail="PHOTO_ROOT not configured")
    return settings.photo_root


def _thumb_dir() -> Path:
    d = settings.thumb_cache_dir or (_photo_root() / ".daruma_thumbs")
    d.mkdir(parents=True, exist_ok=True)
    return d


async def _resolve_path(entity_id: UUID) -> Path:
    """UUID → absolute file path via external_id in DB."""
    async with get_connection() as conn:
        row = await conn.fetchrow(
            "SELECT external_id FROM entities WHERE id = $1 AND type = 'photo'",
            entity_id,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Photo entity not found")

    full_path = _photo_root() / row["external_id"]
    if not full_path.exists():
        raise HTTPException(status_code=404, detail=f"File not on disk: {row['external_id']}")
    return full_path


def _make_thumb(src: Path, dest: Path, size: int) -> None:
    """Generate JPEG thumbnail (runs in thread pool, not on event loop)."""
    from PIL import Image, ImageOps

    if src.suffix.lower() == ".heic":
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except ImportError:
            raise RuntimeError("pillow-heif not installed; cannot thumbnail HEIC")

    with Image.open(src) as img:
        img = ImageOps.exif_transpose(img)   # respect EXIF orientation
        img.thumbnail((size, size), Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(dest, "JPEG", quality=82, optimize=True)


# ---------------------------------------------------------------------------

@router.get("/{entity_id}", summary="Stream original photo file")
async def get_photo(
    entity_id: UUID,
    request: Request,
    api_key: str | None = Query(default=None, description="API key (alt to X-API-Key header)"),
) -> FileResponse:
    """
    Stream the original photo file.

    Accepts auth via `X-API-Key` header **or** `?api_key=` query param.
    """
    _check_auth(request, api_key)
    path = await _resolve_path(entity_id)
    media_type = _MIME.get(path.suffix.lower(), "application/octet-stream")
    return FileResponse(path, media_type=media_type, filename=path.name)


@router.get("/{entity_id}/thumb", summary="Stream thumbnail (lazily cached JPEG)")
async def get_thumb(
    entity_id: UUID,
    request: Request,
    api_key: str | None = Query(default=None, description="API key (alt to X-API-Key header)"),
) -> FileResponse:
    """
    Return a JPEG thumbnail (max THUMB_SIZE px on longest side, default 400).

    Generated on first request, cached to `THUMB_CACHE_DIR/{uuid}.jpg`.
    Accepts auth via `X-API-Key` header **or** `?api_key=` query param —
    use the query param form so `<img src="...?api_key=KEY">` works directly.
    """
    _check_auth(request, api_key)

    thumb_path = _thumb_dir() / f"{entity_id}.jpg"

    if not thumb_path.exists():
        src_path = await _resolve_path(entity_id)
        try:
            await asyncio.get_event_loop().run_in_executor(
                _thumb_executor, _make_thumb, src_path, thumb_path, settings.thumb_size
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Thumbnail generation failed: {e}")

    return FileResponse(thumb_path, media_type="image/jpeg")
