#!/usr/bin/env python3
"""Entrypoint for the scheduled client-data-retention service - see
tidybridge.retention for the actual logic and tidybridge/README.md's
"Data retention" section for how this gets run on a schedule in Railway.

Run manually: python scripts/retention_sweep.py
"""

from __future__ import annotations

import sys

from tidybridge.retention import run

if __name__ == "__main__":
    try:
        run()
    except Exception as exc:  # noqa: BLE001 - a scheduled job's top-level
        # catch-all: any failure here must exit non-zero (so Railway's
        # Cron Schedule marks the run failed) rather than getting
        # swallowed, whatever the underlying exception type turns out to
        # be - a DB connection error looks nothing like a pg_dump
        # failure, unlike backup_db.py's narrower, known exception types.
        print(f"Retention sweep failed: {exc}", file=sys.stderr)
        sys.exit(1)
