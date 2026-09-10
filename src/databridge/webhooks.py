"""Outbound webhook delivery. A failed delivery must never fail the ingest
request that triggered it - the record is already safely persisted by the
time we attempt to notify anyone, so a network blip on the receiving end is
the receiver's problem to retry, not a reason to roll back real data."""

from __future__ import annotations

import httpx
from sqlalchemy.orm import Session

from databridge.config import settings
from databridge.models import ClientRecord, WebhookDelivery

TIMEOUT_SECONDS = 5.0


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
    headers = {}
    if settings.webhook_secret:
        headers["X-Databridge-Secret"] = settings.webhook_secret

    delivery = WebhookDelivery(record_id=record.id, url=settings.webhook_url, success=False)

    try:
        response = httpx.post(
            settings.webhook_url, json=payload, headers=headers, timeout=TIMEOUT_SECONDS
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
