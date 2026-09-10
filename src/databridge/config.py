"""Runtime configuration, read from environment variables (never hardcoded).

Locally these come from a .env file (see .env.example); in production
(Railway) they're set as real environment variables in the project's
dashboard. Either way, the application code never sees a literal secret."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/databridge"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, v: str) -> str:
        """Railway (and most hosts) inject DATABASE_URL as
        postgres://... or postgresql://..., the libpq-style scheme with no
        driver specified. SQLAlchemy then defaults to psycopg2, which isn't
        installed here (this project uses psycopg3, `psycopg[binary]`) and
        would fail with a ModuleNotFoundError - not the Connection refused
        error this was written to fix, but the next thing that would break
        once that one is. Rewriting the scheme here means the same env var
        works locally, in CI, and on any host, regardless of which scheme
        prefix it hands us."""
        if v.startswith("postgres://"):
            return "postgresql+psycopg://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            return "postgresql+psycopg://" + v[len("postgresql://") :]
        return v
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

    jwt_secret: str = "insecure-local-dev-secret-do-not-use-in-production"
    """Signs the JWTs issued at login. The default is intentionally
    obviously-fake so a real deployment that forgets to set this notices
    immediately rather than trusting an unknown value - production sets a
    real random secret via the environment, never committed."""

    github_client_id: str | None = None
    github_client_secret: str | None = None
    """GitHub OAuth App credentials (Settings > Developer settings > OAuth
    Apps in GitHub). None disables the "Sign in with GitHub" flow - email +
    password login still works without them."""


settings = Settings()
