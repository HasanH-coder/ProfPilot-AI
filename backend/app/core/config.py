"""Application settings, read from environment variables or the backend/.env file."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# The backend/ folder (this file is backend/app/core/config.py).
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Environment variables override values in .env; unknown keys in .env are ignored.
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "ProfPilot AI API"

    # Comma-separated list of browser origins allowed to call the API (CORS).
    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
