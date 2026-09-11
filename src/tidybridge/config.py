"""Runtime configuration, read from environment variables (never hardcoded).

Locally these come from a .env file (see .env.example); in production
(Railway) they're set as real environment variables in the project's
dashboard. Either way, the application code never sees a literal secret."""

from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://postgres:postgres@localhost:5432/tidybridge"

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
    webhook_max_attempts: int = 3
    """Total attempts (the first try plus retries) before a delivery is
    given up on. Previously this was a single best-effort POST - a
    receiver's brief outage (a deploy, a cold start, a transient 5xx)
    meant the notification was simply lost, logged but never tried
    again. Each attempt gets its own WebhookDelivery row (see models.py),
    so the audit trail shows every try, not just the last one."""
    webhook_retry_backoff_seconds: float = 1.0
    """Base delay before the first retry; each subsequent retry doubles
    it (1s, 2s, 4s, ... - see webhooks.py's _backoff_seconds). Kept tiny
    in tests via monkeypatching this setting, not by changing the retry
    logic itself. Runs synchronously inside the same upload request
    (already offloaded to a worker thread, see main.py's use of
    run_in_threadpool) rather than as a separate background job - no
    queue/broker infrastructure exists in this project (see README's
    "What it doesn't do (yet)"), so a slow/down receiver adds real
    latency to that one upload response instead of failing silently;
    an explicit, documented tradeoff, not an oversight."""

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

    frontend_url: str = "http://localhost:5173"
    """Origin of the frontend SPA (frontend/). Used two ways: (1) CORS -
    the only origin allowed to call this API with credentials from a
    browser; (2) after a successful GitHub OAuth login, the backend
    redirects the browser here (to `<frontend_url>/auth/callback#access_
    token=...`) instead of returning a bare JSON body - see auth.py's
    RedirectTransport. Vite's dev server default is the localhost value
    above; production sets the real deployed frontend URL."""

    max_upload_size_mb: int = 10
    """Rejects a /records/upload file larger than this (see main.py) - a
    real client data export is nowhere near this size, and without a cap
    the whole file is read into memory before tidycsv/pandas ever sees it,
    which makes an oversized upload a cheap way to flood the service."""

    backup_dir: str = "/data/backups"
    """Where scripts/backup_db.py writes dumps - a Railway Volume mounted
    on the backup service (see README's Backups section), not the app's
    own container filesystem, which is wiped on every deploy. Doesn't
    protect against losing the whole Railway project/account, only against
    a mistake or corruption inside the database itself - documented as
    that explicit tradeoff, not glossed over."""
    backup_retention_days: int = 30
    """backup_db.py deletes dump files older than this on every run, so
    the volume doesn't grow forever - not a substitute for actual
    point-in-time recovery, just bounding how much history is kept."""

    client_data_retention_days: int = 365
    """scripts/retention_sweep.py deletes IngestionRun and ClientRecord
    rows older than this, on every run - see README's "Data retention"
    section. Applies only to client data (what an engineer uploads about
    their own clients), not to an engineer's own account, which is kept
    until they delete it themselves (DELETE /users/me)."""

    resend_api_key: str | None = None
    """Resend (resend.dev) API key, used by auth.py's UserManager to send
    forgot-password emails. None disables real delivery - the reset link
    is logged instead (see _send_email in auth.py), which is fine for
    local dev but must be set in production or nobody can actually
    receive one."""
    email_from: str = "tidybridge <onboarding@resend.dev>"
    """Resend's shared onboarding@resend.dev sender - works without
    verifying a custom domain, but ONLY to the Resend account's own
    email address (confirmed against real production sends: a plain
    recipient and a Gmail "+"-tagged one both failed silently - Resend's
    API 403s with "You can only send testing emails to your own email
    address", see https://resend.com/docs/knowledge-base/403-error-resend-dev-domain
    - _send_email's except clause swallows that the same as any other
    delivery failure, by design, so nothing user-facing breaks). Real
    delivery to anyone else needs a verified domain - swap this for a
    verified-domain address once one exists; until then, production
    email only actually reaches the account owner."""

    enable_api_docs: bool = True
    """Whether FastAPI's own interactive docs (/docs, /redoc) and the raw
    schema (/openapi.json) are served at all (see main.py). Every route
    those describe still enforces its own auth regardless of this - it's
    not a security boundary - but leaving the full API surface publicly
    browsable is free reconnaissance for no real benefit once the API is
    actually live. Defaults True for local development convenience;
    production sets this to False via Railway's environment variables."""


settings = Settings()
