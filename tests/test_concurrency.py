"""Regression test for a real scalability/performance bug found during an
architecture review: POST /records/upload is declared `async def` (needed
for `await file.read()`), but used to call ingest_file() - CSV parsing,
several synchronous DB round-trips, and (via notify_new_record) a blocking
httpx.post to the webhook receiver - directly, instead of offloading it.
FastAPI only auto-offloads *sync* `def` routes to a worker thread; a sync
call made directly inside an async route runs on the single event loop
thread instead. With a single uvicorn worker (this project's Dockerfile has
no --workers flag), that meant one upload stalled every other in-flight
request on the whole process for as long as ingest_file took, not just the
uploader's own request.

This is checked deterministically, not by racing timers: which real OS
thread actually executes ingest_file, compared to the thread driving the
event loop the route handler runs on. An earlier version of this test tried
to reproduce the resulting stall by timing a concurrent request against a
deliberately slow webhook receiver; that turned out to be an unreliable
reproduction (real task-scheduling/await-ordering details let the "outer"
thread service other work even in the buggy version, when the timing
happened to line up right) - the thread-identity check below is what the
underlying fix (starlette.concurrency.run_in_threadpool) actually
guarantees, so it's what's actually worth asserting.

The webhook leg is still exercised end to end against a real local HTTP
server (not a mock, same pattern as test_webhooks.py) so the whole
ingest_file path - including the part that used to be the actual
blocking-call culprit - is what gets thread-checked, not just the CSV
parsing."""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tidybridge.main import app


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - http.server's own naming convention
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # silence default request logging
        pass


@pytest.fixture
def webhook_receiver(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    import tidybridge.config as config_module

    monkeypatch.setattr(config_module.settings, "webhook_url", f"http://127.0.0.1:{port}/")
    monkeypatch.setattr(config_module.settings, "webhook_secret", None)

    yield
    server.shutdown()


@pytest.mark.asyncio
async def test_upload_offloads_ingest_off_the_event_loop_thread(
    client: TestClient, webhook_receiver, monkeypatch
):
    import tidybridge.main as main_module

    # httpx's ASGITransport calls the app in-process on whatever event loop
    # is currently running - no separate portal thread involved (unlike the
    # sync TestClient fixture) - so the thread ident captured here, before
    # any await, *is* the thread that will drive the route handler's event
    # loop for this request.
    event_loop_thread_ident = threading.get_ident()

    recorded: dict[str, int] = {}
    original_ingest_file = main_module.ingest_file

    def _recording_ingest_file(*args, **kwargs):
        recorded["thread_ident"] = threading.get_ident()
        return original_ingest_file(*args, **kwargs)

    monkeypatch.setattr(main_module, "ingest_file", _recording_ingest_file)

    fixture = Path(__file__).parent.parent / "examples" / "messy_clients.csv"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver", headers=client.headers
    ) as async_client:
        resp = await async_client.post(
            "/records/upload",
            files={"file": ("messy_clients.csv", fixture.read_bytes(), "text/csv")},
        )

    assert resp.status_code == 200
    assert "thread_ident" in recorded, "ingest_file was never called"
    assert recorded["thread_ident"] != event_loop_thread_ident, (
        "ingest_file ran on the exact same OS thread driving this request's "
        "event loop instead of being offloaded to a worker thread - on a "
        "real single-worker uvicorn process, that stalls every other "
        "in-flight request for as long as ingest_file takes"
    )
