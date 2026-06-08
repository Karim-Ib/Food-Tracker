from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# This file lives at api/app/config.py — go up three levels to project root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Postgres connection
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "foodbot"
    app_user_password: str

    @property
    def app_db_url_sync(self) -> str:
        """For Alembic — uses psycopg (sync driver)."""
        return (
            f"postgresql+psycopg://app_user:{self.app_user_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def app_db_url_async(self) -> str:
        """For FastAPI — uses asyncpg (async driver)."""
        return (
            f"postgresql+asyncpg://app_user:{self.app_user_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()