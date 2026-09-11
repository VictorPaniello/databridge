"""Background delivery worker for the webhook_jobs queue (webhooks.py's
enqueue_delivery() writes to it, models.py's WebhookJob defines it). Runs
as its own long-lived process (see scripts/webhook_worker.py), separate
from the request/response cycle - the whole reason this exists is so a
slow or dead receiver can never add latency to POST /records/upload (see
main.py's comment on why that route uses run_in_threadpool for the CSV/DB
work that's still done inline).

ponytail: claims one job at a time (SELECT ... FOR UPDATE SKIP LOCKED
LIMIT 1, committed before claiming the next) rather than batching the
claim query - a batched claim would hold every claimed row's lock only
until the *first* job's own commit, silently letting a second worker
process claim the rest of the "still in progress" batch. One-at-a-time
costs an extra round trip per job; worth it for correctness at this
project's throughput. Move to a two-phase claim (mark 'processing' +
commit, then deliver, then commit the final status) if this ever needs
to batch for real throughput."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.models import ClientRecord, WebhookJob
from databridge.webhooks import _backoff_seconds, deliver_attempt


def process_due_jobs(db: Session, limit: int = 20) -> int:
    """Claims and works through up to `limit` due jobs (status="pending",
    available_at in the past), one at a time. Returns how many it
    processed (not how many succeeded - a job that failed and was
    rescheduled, or marked dead, still counts)."""
    processed = 0
    for _ in range(limit):
        job = db.execute(
            select(WebhookJob)
            .where(WebhookJob.status == "pending", WebhookJob.available_at <= datetime.now(UTC))
            .order_by(WebhookJob.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).scalar_one_or_none()
        if job is None:
            break

        # Guaranteed to exist: ON DELETE CASCADE on webhook_jobs.record_id
        # (see the Task 1 migration) means a DELETE /records/{id} that
        # removes this record has to acquire a lock on this job row too -
        # it blocks behind the FOR UPDATE above until this transaction
        # commits, by which point this job is already done/dead and about
        # to be deleted right along with the record it points at.
        record = db.get(ClientRecord, job.record_id)

        delivery = deliver_attempt(db, record, job.attempt_number)
        if delivery.success:
            job.status = "done"
        elif job.attempt_number >= settings.webhook_max_attempts:
            job.status = "dead"
        else:
            delay = _backoff_seconds(job.attempt_number)
            job.attempt_number += 1
            job.available_at = datetime.now(UTC) + timedelta(seconds=delay)
        db.commit()
        processed += 1
    return processed
