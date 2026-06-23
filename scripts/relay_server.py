"""Self-hosted Knitweb relay server — drop-in replacement for the 5mart.ml PHP relay.

Implements the same store-and-forward mailbox API that RelayTransport uses:
  POST /api/relay/send   — deposit a frame into a named mailbox
  POST /api/relay/fetch  — drain frames (long-poll, 20 s timeout)
  GET  /api/relay/health — operator liveness check + metric snapshot

Run on your own VPS with a public IP:
    PYTHONPATH=src uvicorn scripts.relay_server:app --host 0.0.0.0 --port 8443 --ssl-keyfile key.pem --ssl-certfile cert.pem

Or behind nginx:
    uvicorn scripts.relay_server:app --host 127.0.0.1 --port 8765

Then point RelayTransport at your server:
    transport = RelayTransport("https://your-server.example.com", my_addr, priv)

The relay is a **dumb pipe**: it never decodes payloads. The only data it
reads is the mailbox name (plain string) and the base64-wrapped opaque frame.
All cryptography, CID verification, and business logic live in the nodes.

Mailbox lifecycle:
  - Mailboxes are ephemeral in-memory queues (deque, max 1 000 frames each).
  - A mailbox that has not been fetched for MAILBOX_TTL_S is reaped by a
    background task so a crashed client does not leak memory forever.
  - No persistence: the relay does not survive a restart. Nodes must
    re-register and re-sync after relay restart (same as a NAT rebind).
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Deque, Dict, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Configuration ─────────────────────────────────────────────────────────────
MAILBOX_TTL_S   = 300        # reap mailbox idle for 5 minutes
MAILBOX_CAP     = 1_000      # max queued frames per mailbox
LONG_POLL_S     = 20.0       # fetch blocks up to this long for new frames
REAP_INTERVAL_S = 60         # background reap cadence
MAX_FRAME_B64   = 12_582_912 # 12 MiB base64 ≈ 9 MiB wire (MAX_FRAME_BYTES * 4/3)

app = FastAPI(title="Knitweb Relay", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["POST", "GET"],
                   allow_headers=["Content-Type"])

# ── In-memory mailbox store ───────────────────────────────────────────────────
class _Mailbox:
    __slots__ = ("frames", "event", "last_fetch", "created_at")

    def __init__(self) -> None:
        self.frames: Deque[str] = deque(maxlen=MAILBOX_CAP)
        self.event = asyncio.Event()
        self.last_fetch: float = time.monotonic()
        self.created_at: float = time.monotonic()

_mailboxes: Dict[str, _Mailbox] = {}

# ── Metrics (integer-only, mirrors knitweb.p2p.metrics vocabulary) ────────────
_counters: Dict[str, int] = {
    "frames_received":    0,   # POST /send calls accepted
    "frames_delivered":   0,   # frames drained from mailboxes
    "frames_dropped":     0,   # frames rejected (mailbox full / oversized)
    "fetches_total":      0,   # POST /fetch calls
    "fetches_timeout":    0,   # fetches that returned empty (long-poll timeout)
    "mailboxes_reaped":   0,   # mailboxes removed by the reaper
}
_started_at: float = time.monotonic()


def _incr(name: str, delta: int = 1) -> None:
    _counters[name] = _counters.get(name, 0) + delta


def _snapshot() -> dict:
    now = time.monotonic()
    return {
        "uptime_s":        int(now - _started_at),
        "mailboxes_live":  len(_mailboxes),
        **_counters,
    }


# ── API models ────────────────────────────────────────────────────────────────
class SendBody(BaseModel):
    mailbox: str
    frame:   str   # base64-encoded opaque frame from write_frame_bytes()


class FetchBody(BaseModel):
    mailbox: str


# ── Routes ────────────────────────────────────────────────────────────────────
@app.post("/api/relay/send")
async def relay_send(body: SendBody) -> JSONResponse:
    if not body.mailbox or len(body.mailbox) > 128:
        raise HTTPException(400, "invalid mailbox name")
    if len(body.frame) > MAX_FRAME_B64:
        _incr("frames_dropped")
        raise HTTPException(413, f"frame too large (>{MAX_FRAME_B64} b64 chars)")

    mb = _mailboxes.setdefault(body.mailbox, _Mailbox())
    if len(mb.frames) >= MAILBOX_CAP:
        _incr("frames_dropped")
        raise HTTPException(503, "mailbox full")

    mb.frames.append(body.frame)
    mb.event.set()
    _incr("frames_received")
    return JSONResponse({"ok": True, "queued": len(mb.frames)})


@app.post("/api/relay/fetch")
async def relay_fetch(body: FetchBody) -> JSONResponse:
    _incr("fetches_total")
    mb = _mailboxes.get(body.mailbox)
    if mb is None:
        mb = _mailboxes.setdefault(body.mailbox, _Mailbox())

    # Drain what's already there
    frames: list[str] = []
    while mb.frames:
        frames.append(mb.frames.popleft())

    # Long-poll if nothing arrived yet
    if not frames:
        mb.event.clear()
        try:
            await asyncio.wait_for(mb.event.wait(), timeout=LONG_POLL_S)
            while mb.frames:
                frames.append(mb.frames.popleft())
        except asyncio.TimeoutError:
            _incr("fetches_timeout")

    mb.last_fetch = time.monotonic()
    _incr("frames_delivered", len(frames))
    return JSONResponse({"ok": True, "frames": frames})


@app.get("/api/relay/health")
async def health() -> JSONResponse:
    return JSONResponse({"status": "ok", "metrics": _snapshot()})


# ── Background reaper ─────────────────────────────────────────────────────────
@app.on_event("startup")
async def _start_reaper() -> None:
    async def _reap() -> None:
        while True:
            await asyncio.sleep(REAP_INTERVAL_S)
            cutoff = time.monotonic() - MAILBOX_TTL_S
            stale = [k for k, mb in _mailboxes.items() if mb.last_fetch < cutoff]
            for k in stale:
                del _mailboxes[k]
            if stale:
                _incr("mailboxes_reaped", len(stale))
    asyncio.create_task(_reap())
