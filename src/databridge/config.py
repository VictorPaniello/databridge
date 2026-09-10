"""Runtime configuration, read from environment variables (never hardcoded).

Locally these come from a .env file (see .env.example); in production
(Railway) they're set as real environment variables in the project's
dashboard. Either way, the application code never sees a literal secret."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/databridge"
    webhook_url: str | None = None
    """Where to POST a notification when a new record is ingested. If unset,
    webhook delivery is skipped entirely (logged, not silently dropped)."""
    webhook_secret: str | None = None
    """Shared secret sent as a header on every webhook delivery, so the
    receiver can verify the request actually came from this service."""

    schema_path: str = "examples/schema.yaml"
    """Path to the tidycsv schema, resolved relative to the process's
    working directory at startup - NOT relative to this source file. A
    __file__-relative path breaks the moment the package is pip-installed
    normally (as in the Docker image) instead of run from an editable
    checkout, because the installed package ends up in site-packages while
    examples/ is not part of it. The Docker image sets its WORKDIR to where
    examples/ was copied, so the default here resolves correctly there too."""


settings = Settings()
