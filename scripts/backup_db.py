#!/usr/bin/env python3
"""Entrypoint for the scheduled backup service - see databridge.backup for
the actual logic and databridge/README.md's "Backups" section for how
this gets run on a schedule in Railway.

Run manually: python scripts/backup_db.py
"""

from __future__ import annotations

import subprocess
import sys

from databridge.backup import run

if __name__ == "__main__":
    try:
        run()
    except (RuntimeError, subprocess.SubprocessError) as exc:
        print(f"Backup failed: {exc}", file=sys.stderr)
        sys.exit(1)
