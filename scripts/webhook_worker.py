#!/usr/bin/env python3
"""Entrypoint for the background webhook-delivery worker - see
databridge.webhook_worker for the actual logic. Unlike scripts/
backup_db.py and scripts/retention_sweep.py (one-shot jobs run on a Cron
Schedule), this runs continuously: deploy it as its own long-lived
Railway service (a Start Command, not a Cron Schedule), restarted by
Railway itself if it ever crashes.

Run manually: python scripts/webhook_worker.py
"""

from __future__ import annotations

import sys
import time

from databridge.db import SessionLocal
from databridge.webhook_worker import process_due_jobs

POLL_INTERVAL_SECONDS = 2.0


def main() -> None:
    print(f"webhook worker started, polling every {POLL_INTERVAL_SECONDS}s")
    while True:
        db = SessionLocal()
        try:
            processed = process_due_jobs(db)
            if processed:
                print(f"processed {processed} webhook job(s)")
        finally:
            db.close()
        time.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
