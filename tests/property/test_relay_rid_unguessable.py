"""Proof: relay dial reply-ids are unguessable capabilities.

A relay reply frame carries no authenticated sender — ``RelayTransport._dispatch``
resolves a pending dial purely by matching ``_relay_rid``. The id used to come
from ``itertools.count(1)`` (sequential, trivially guessable), so anyone able to
write to the dialer's mailbox could deposit a frame with a guessed in-flight rid
and no ``_relay_reply_to`` and resolve a pending ``dial()`` with attacker-chosen
content (response spoofing / DoS). The id is now an unguessable 63-bit value, so
guessing an in-flight rid is infeasible and the rid acts as an unforgeable
capability only the legitimate responder learns.
"""

import asyncio

import pytest

from knitweb.p2p.relay import RelayTransport, _b64encode
from knitweb.p2p.wire import write_frame_bytes


def run(coro):
    return asyncio.run(coro)


def _frame_msg(payload: dict) -> dict:
    return {"frame": _b64encode(write_frame_bytes(payload))}


@pytest.mark.property
def test_new_rid_is_unguessable_and_distinct():
    transport = RelayTransport(base_url="https://5mart.ml", mailbox="me", poster=None)
    rids = [transport._new_rid() for _ in range(64)]
    # Distinct (collision-avoided) and NOT the old sequential 1..N series.
    assert len(set(rids)) == len(rids)
    assert set(rids) != set(range(1, len(rids) + 1))
    # High-entropy: at least one draw uses the upper bits (sequential ids never
    # would). The chance all 64 draws fall under 2**40 is ~ (2**-23)**64 ≈ 0.
    assert max(r.bit_length() for r in rids) >= 40
    assert all(0 <= r < (1 << 63) for r in rids)


@pytest.mark.property
def test_spoofed_sequential_rid_cannot_resolve_a_pending_dial():
    async def scenario():
        transport = RelayTransport(base_url="https://5mart.ml", mailbox="me", poster=None)
        real_rid = transport._new_rid()
        loop = asyncio.get_running_loop()
        waiter = loop.create_future()
        transport._waiters[real_rid] = waiter

        # An attacker who can write to our mailbox sprays guessed sequential rids
        # with no _relay_reply_to (the reply branch). None resolve our waiter.
        for guess in range(1, 256):
            await transport._dispatch(_frame_msg({"_relay_rid": guess, "spoofed": True}))
            assert not waiter.done(), f"sequential rid {guess} must not resolve the dial"

        # The legitimate responder replies with the real (unguessable) rid and the
        # waiter resolves with the stripped business payload.
        await transport._dispatch(_frame_msg({"_relay_rid": real_rid, "ok": True}))
        assert waiter.done()
        assert waiter.result() == {"ok": True}

    run(scenario())


@pytest.mark.property
def test_new_rid_skips_an_already_pending_rid(monkeypatch):
    # Two concurrent dials must never share a waiter: if a fresh draw collides
    # with a pending rid, _new_rid retries. Stub randbits to force a collision.
    import knitweb.p2p.relay as relay_mod

    transport = RelayTransport(base_url="https://5mart.ml", mailbox="me", poster=None)
    transport._waiters[111] = None  # _new_rid only checks key membership
    draws = iter([111, 222])  # first collides with the pending waiter, second is free
    monkeypatch.setattr(relay_mod.secrets, "randbits", lambda _n: next(draws))

    assert transport._new_rid() == 222
