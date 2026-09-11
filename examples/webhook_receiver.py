"""Reference webhook receiver: what an integration partner's server should
actually do with what tidybridge sends it. Not part of the deployed
service - a standalone example, runnable on its own to test against a
local tidybridge instance.

Usage:
    pip install fastapi uvicorn
    python examples/webhook_receiver.py
    # then run tidybridge with:
    #   WEBHOOK_URL=http://127.0.0.1:9099/webhook
    #   WEBHOOK_SECRET=<the same value as WEBHOOK_SECRET below>
"""

from __future__ import annotations

import hashlib
import hmac

import uvicorn
from fastapi import FastAPI, HTTPException, Request

# Must match the receiving service's WEBHOOK_SECRET exactly - shared out
# of band between the two systems, never sent over the wire itself.
WEBHOOK_SECRET = "replace-with-the-real-shared-secret"

app = FastAPI(title="tidybridge webhook receiver (example)")


def verify_signature(body: bytes, signature_header: str | None) -> None:
    """Recomputes the HMAC over the raw body actually received and
    compares it to the header - the same thing tidybridge does when
    sending, so a mismatch means either the wrong secret or the body was
    altered in transit. hmac.compare_digest, not `==`, so the comparison
    itself doesn't leak timing information about how much of the
    signature matched."""
    if not signature_header or not signature_header.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Missing or malformed signature")

    expected = "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature_header):
        raise HTTPException(status_code=401, detail="Signature does not match")


@app.post("/webhook")
async def receive_webhook(request: Request) -> dict:
    body = await request.body()
    verify_signature(body, request.headers.get("X-Tidybridge-Signature-256"))

    payload = await request.json()
    print(f"Verified event: {payload['event']} for record {payload['record']['id']}")
    return {"received": True}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=9099)
