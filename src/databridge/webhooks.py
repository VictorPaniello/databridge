"""Outbound webhook delivery. A failed delivery must never fail the ingest
request that triggered it - the record is already safely persisted by the
time we attempt to notify anyone, so a network blip on the receiving end is
the receiver's problem to retry, not a reason to roll back real data.

Previously a single best-effort POST: one attempt, logged whether it
succeeded or not, never tried again. A receiver's brief outage (a deploy,
a cold start, a transient 5xx) meant the notification was simply lost.
notify_new_record() now retries with exponential backoff, up to
settings.webhook_max_attempts total tries, persisting one WebhookDelivery
row per attempt so the audit trail shows the full retry history."""

from __future__ import annotations

import hashlib
import hmac
import json
import time

import httpx
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.models import ClientRecord, WebhookDelivery

TIMEOUT_SECONDS = 5.0


def _backoff_seconds(attempt: int) -> float:
    """Delay before retrying after `attempt` (1-based) has failed: base,
    2x base, 4x base, ... - settings.webhook_retry_backoff_seconds is the
    base, so tests can shrink it to keep a retry test fast without
    changing this formula."""
    return settings.webhook_retry_backoff_seconds * (2 ** (attempt - 1))


def sign_payload(body: bytes, secret: str) -> str:
    """HMAC-SHA256 over the exact bytes sent, not a re-serialization of the
    payload dict - the receiver must be able to verify the signature
    against the literal request body it received, the same pattern Stripe
    and GitHub use for their webhooks. Previously this sent the raw secret
    itself as a header value (X-Databridge-Secret) - a weaker design: it
    puts the actual secret on the wire on every delivery instead of only
    ever using it locally to compute/verify a signature, and gives a
    receiver no way to confirm the body wasn't tampered with in transit."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver_attempt(db: Session, record: ClientRecord, attempt_number: int) -> WebhookDelivery:
    """One HTTP attempt: builds and signs the payload, POSTs it, persists
    and commits exactly one WebhookDelivery row recording the outcome.
    Shared by notify_new_record()'s manual-replay retry loop below and
    webhook_worker.py's process_due_jobs() - the only difference between
    an automatic (queued) attempt and a replay's is who calls this and
    how the next attempt (if any) gets scheduled, not what one attempt
    itself does."""
    payload = {
        "event": "client_record.created",
        "record": {
            "id": str(record.id),
            "email": record.email,
            "full_name": record.full_name,
            "has_issues": record.has_issues,
            "issues": record.issues,
        },
    }
    # Serialized once, here - so the signature is computed over the exact
    # bytes that get sent, rather than trusting httpx's own json= encoding
    # to produce identical bytes to whatever we signed separately.
    body = json.dumps(payload).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        headers["X-Databridge-Signature-256"] = sign_payload(body, settings.webhook_secret)

    delivery = WebhookDelivery(
        record_id=record.id, url=settings.webhook_url, success=False, attempt_number=attempt_number
    )
    try:
        response = httpx.post(
            settings.webhook_url, content=body, headers=headers, timeout=TIMEOUT_SECONDS
        )
        delivery.status_code = response.status_code
        delivery.success = response.is_success
        if not response.is_success:
            delivery.error = f"non-2xx response: {response.status_code}"
    except httpx.HTTPError as exc:
        delivery.error = f"{type(exc).__name__}: {exc}"

    db.add(delivery)
    db.commit()
    return delivery


def notify_new_record(db: Session, record: ClientRecord) -> WebhookDelivery | None:
    """Manual, on-demand replay only (POST /records/{id}/webhooks/replay,
    main.py) - the automatic post-ingest notification goes through
    enqueue_delivery() and the background worker instead (see
    webhook_worker.py). Kept synchronous deliberately: a replay is a
    human asking for an immediate resend mid-incident, not something that
    should wait behind the queue's own poll interval."""
    if not settings.webhook_url:
        return None  # no receiver configured - nothing to do, not an error

    delivery: WebhookDelivery | None = None
    for attempt in range(1, settings.webhook_max_attempts + 1):
        delivery = deliver_attempt(db, record, attempt)
        if delivery.success:
            return delivery
        if attempt < settings.webhook_max_attempts:
            time.sleep(_backoff_seconds(attempt))

    # Every attempt failed - the last delivery row (already persisted
    # above, success=False) is the one callers get back, the same
    # contract as before this refactor.
    return delivery
