from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    database_url: str = "postgresql://daruma:daruma_dev@localhost:5432/daruma"
    api_key: str = "dev-api-key"
    host: str = "0.0.0.0"
    port: int = 8000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
