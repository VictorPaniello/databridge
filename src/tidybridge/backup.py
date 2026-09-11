"""Dumps the database with pg_dump into settings.backup_dir - a Railway
Volume mounted on a dedicated backup service, not the app's own container
filesystem (which is wiped on every deploy) - then deletes dump files
older than settings.backup_retention_days.

Explicit tradeoff: this protects against a mistake or corruption inside
the database itself (an accidental DROP TABLE, a bad migration), not
against losing the whole Railway project/account - a real off-platform
backup (S3-compatible storage) would be the stronger answer, deliberately
not done here because it required a cloud account the user didn't want to
create.

The actual entrypoint is scripts/backup_db.py, which just calls run()
below - the logic lives in the installed package (not the standalone
script) so it can be imported and tested the same way as everything else
in this project."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tidybridge.config import settings

BACKUP_PREFIX = "tidybridge-backup-"


def _pg_dump_connection_string() -> str:
    """pg_dump doesn't understand SQLAlchemy's "+psycopg" driver suffix -
    it wants the plain libpq URL scheme."""
    return settings.database_url.replace("postgresql+psycopg://", "postgresql://", 1)


def create_dump(destination: Path) -> None:
    """-Fc: PostgreSQL's own "custom" format - compressed, and restorable
    with pg_restore (including selectively, table by table), unlike a
    plain SQL dump. Raises on any non-zero exit rather than trusting
    pg_dump's own text output - a partial or corrupt dump must fail the
    whole job, not silently get treated as a successful backup."""
    result = subprocess.run(
        ["pg_dump", "-Fc", "-f", str(destination), _pg_dump_connection_string()],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"pg_dump failed (exit {result.returncode}): {result.stderr}")


def delete_old_backups(backup_dir: Path) -> list[Path]:
    """Returns the paths it deleted, so the caller can log what happened -
    a backup job that silently deletes things is exactly the kind of
    "trust me" behavior this whole feature exists to avoid. Uses each
    file's mtime, not a timestamp parsed back out of its name - correct
    even if a file was ever renamed or copied in some other way."""
    cutoff = datetime.now(UTC) - timedelta(days=settings.backup_retention_days)
    deleted: list[Path] = []

    for path in backup_dir.glob(f"{BACKUP_PREFIX}*.dump"):
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC)
        if mtime < cutoff:
            path.unlink()
            deleted.append(path)
    return deleted


def run() -> None:
    backup_dir = Path(settings.backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    dump_path = backup_dir / f"{BACKUP_PREFIX}{timestamp}.dump"

    print(f"Dumping database to {dump_path}...")
    create_dump(dump_path)
    size_mb = dump_path.stat().st_size / (1024 * 1024)
    print(f"Dump complete: {size_mb:.2f} MB")

    deleted = delete_old_backups(backup_dir)
    if deleted:
        names = ", ".join(p.name for p in deleted)
        print(
            f"Deleted {len(deleted)} backup(s) older than "
            f"{settings.backup_retention_days} days: {names}"
        )
    else:
        print("No old backups to delete.")
