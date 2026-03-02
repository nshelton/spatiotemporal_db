from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql://daruma:daruma_dev@localhost:5432/daruma"
    api_key: str = "dev-api-key"
    host: str = "0.0.0.0"
    port: int = 8000

    # Photo serving
    photo_root: Path | None = None          # e.g. D:/Photos
    thumb_cache_dir: Path | None = None     # defaults to photo_root/.daruma_thumbs
    thumb_size: int = 400                   # max dimension in pixels

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
