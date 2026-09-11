"""Runs the real pg_dump against the real test database and writes real
files to a real (temporary) directory - no mocks needed now that backups
go to a local/volume path instead of a remote bucket."""

from __future__ import annotations

import os
import subprocess
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from tidybridge.backup import (
    BACKUP_PREFIX,
    _pg_dump_connection_string,
    create_dump,
    delete_old_backups,
)


def test_pg_dump_connection_string_strips_the_sqlalchemy_driver_suffix(monkeypatch):
    import tidybridge.config as config_module

    monkeypatch.setattr(
        config_module.settings,
        "database_url",
        "postgresql+psycopg://user:pass@host:5432/db",
    )
    assert _pg_dump_connection_string() == "postgresql://user:pass@host:5432/db"


def test_create_dump_produces_a_real_restorable_dump():
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "test.dump"
        create_dump(dest)

        assert dest.exists()
        assert dest.stat().st_size > 0

        # Proves it's a real, valid pg_dump archive, not just some bytes -
        # pg_restore --list parses the archive's table of contents without
        # actually restoring anything.
        result = subprocess.run(
            ["pg_restore", "--list", str(dest)], capture_output=True, text=True
        )
        assert result.returncode == 0
        assert "client_records" in result.stdout
        assert "webhook_deliveries" in result.stdout


def test_create_dump_raises_on_a_bad_connection(monkeypatch):
    import tidybridge.config as config_module

    monkeypatch.setattr(
        config_module.settings,
        "database_url",
        "postgresql+psycopg://postgres:postgres@127.0.0.1:59999/nonexistent",
    )
    with tempfile.TemporaryDirectory() as tmp:
        with pytest.raises(RuntimeError, match="pg_dump failed"):
            create_dump(Path(tmp) / "test.dump")


def test_delete_old_backups_only_deletes_past_the_retention_window(monkeypatch):
    import tidybridge.config as config_module

    monkeypatch.setattr(config_module.settings, "backup_retention_days", 30)

    with tempfile.TemporaryDirectory() as tmp:
        backup_dir = Path(tmp)
        old_file = backup_dir / f"{BACKUP_PREFIX}old.dump"
        recent_file = backup_dir / f"{BACKUP_PREFIX}recent.dump"
        old_file.write_bytes(b"old")
        recent_file.write_bytes(b"recent")

        old_time = (datetime.now(UTC) - timedelta(days=45)).timestamp()
        recent_time = (datetime.now(UTC) - timedelta(days=2)).timestamp()
        os.utime(old_file, (old_time, old_time))
        os.utime(recent_file, (recent_time, recent_time))

        deleted = delete_old_backups(backup_dir)

        assert deleted == [old_file]
        assert not old_file.exists()
        assert recent_file.exists()


def test_delete_old_backups_ignores_files_not_matching_the_backup_prefix(tmp_path):
    unrelated = tmp_path / "not-a-backup.txt"
    unrelated.write_bytes(b"leave me alone")
    old_time = (datetime.now(UTC) - timedelta(days=999)).timestamp()
    os.utime(unrelated, (old_time, old_time))

    deleted = delete_old_backups(tmp_path)

    assert deleted == []
    assert unrelated.exists()
