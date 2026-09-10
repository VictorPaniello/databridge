"""Outbound webhook delivery. A failed delivery must never fail the ingest
request that triggered it - the record is already safely persisted by the
time we attempt to notify anyone, so a network blip on the receiving end is
the receiver's problem to retry, not a reason to roll back real data."""

from __future__ import annotations

import hashlib
import hmac
import json

import httpx
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.models import ClientRecord, WebhookDelivery

TIMEOUT_SECONDS = 5.0


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


def notify_new_record(db: Session, record: ClientRecord) -> WebhookDelivery | None:
    if not settings.webhook_url:
        return None  # no receiver configured - nothing to do, not an error

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

    delivery = WebhookDelivery(record_id=record.id, url=settings.webhook_url, success=False)

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
