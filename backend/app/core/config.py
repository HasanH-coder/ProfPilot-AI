"""Application settings, read from environment variables or the backend/.env file."""

from functools import cached_property
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# The backend/ folder (this file is backend/app/core/config.py).
BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    # Environment variables override values in .env; unknown keys in .env are ignored.
    model_config = SettingsConfigDict(env_file=BACKEND_DIR / ".env", extra="ignore")

    app_name: str = "ProfPilot AI API"

    # Comma-separated list of browser origins allowed to call the API (CORS).
    cors_origins: str = "http://localhost:3000"

    # --- Supabase -----------------------------------------------------------
    # Both values are public (the browser has them too). The API calls Supabase
    # with the professor's own access token, so Row Level Security applies to
    # everything it reads or writes. No secret or service_role key is used.
    supabase_url: str = ""
    supabase_publishable_key: str = ""

    # --- OpenAI -------------------------------------------------------------
    # Server-only. SecretStr keeps the key out of logs, reprs and error messages.
    openai_api_key: SecretStr | None = None
    # The models, in one place. Each can be overridden with an environment
    # variable of the same name, e.g. REASONING_MODEL=...
    reasoning_model: str = "gpt-6.1-sol"
    realtime_model: str = "gpt-realtime-2.1"
    embedding_model: str = "text-embedding-3-small"
    # Must match the vector(1536) column in the document_chunks table.
    embedding_dimensions: int = 1536
    realtime_voice: str = "marin"
    realtime_transcription_model: str = "gpt-live-transcribe"
    # How long a browser's voice credential stays valid before a call must start
    # (the call itself may last longer). OpenAI accepts 10–7200 seconds.
    realtime_client_secret_seconds: int = 120
    openai_timeout_seconds: float = 600.0
    openai_max_retries: int = 2

    # --- Limits -------------------------------------------------------------
    max_extracted_characters: int = 2_000_000
    max_ocr_pages: int = 30
    max_concurrent_generation_calls: int = 3

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @cached_property
    def supabase_issuer(self) -> str:
        return f"{self.supabase_url.rstrip('/')}/auth/v1"

    @cached_property
    def supabase_jwks_url(self) -> str:
        return f"{self.supabase_issuer}/.well-known/jwks.json"

    @property
    def is_supabase_configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_publishable_key)

    @property
    def is_openai_configured(self) -> bool:
        return bool(self.openai_api_key and self.openai_api_key.get_secret_value())


settings = Settings()
