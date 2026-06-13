from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    telegram_bot_token: str

    api_base_url: str = "http://localhost:8000"
    api_timeout_s: float = 10.0

    log_level: str = "INFO"

settings = Settings()