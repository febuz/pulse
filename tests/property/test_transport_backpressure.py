"""Backpressure / DoS guards on the TCP carrier (gap c).

``TcpTransport.listen`` must not let a connection flood or a slow-loris peer
exhaust the node. These tests pin the carrier-level guards added to the accept
loop, all of which are deterministic integer policy knobs that never touch the
wire framing bytes (so signed-record byte-identity is unaffected — that gate
lives in ``test_transport_roundtrip.py``):

  * a bounded concurrent-connection semaphore caps how many inbound connections
    are *served* at once;
  * a per-connection single-frame read deadline drops a peer that stalls
    mid-frame, freeing its slot.
"""

import asyncio

import pytest

from knitweb.p2p.transport import (
    DEFAULT_MAX_INBOUND,
    DEFAULT_READ_TIMEOUT_S,
    TcpTransport,
)
from knitweb.p2p.wire import read_frame, write_frame


@pytest.mark.property
def test_default_limits_are_positive_integers():
    # Deterministic integer knobs, no randomness / wall-clock in the defaults.
    assert isinstance(DEFAULT_MAX_INBOUND, int) and DEFAULT_MAX_INBOUND >= 1
    assert isinstance(DEFAULT_READ_TIMEOUT_S, int) and DEFAULT_READ_TIMEOUT_S >= 1


@pytest.mark.property
@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_caps_rejected(bad):
    with pytest.raises(ValueError):
        TcpTransport(max_inbound=bad)
    with pytest.raises(ValueError):
        TcpTransport(read_timeout_s=bad)


def test_inbound_semaphore_caps_concurrency():
    """No more than ``max_inbound`` handlers run the user handler at once."""

    async def run() -> None:
        live = 0
        peak = 0
        gate = asyncio.Event()

        async def handler(request: dict) -> dict:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            # Hold every in-flight handler until the test releases the gate, so
            # the peak concurrency reflects the semaphore cap, not scheduling.
            await gate.wait()
            live -= 1
            return {"ok": True}

        transport = TcpTransport(host="127.0.0.1", port=0, max_inbound=2)
        await transport.listen(handler)
        host, port = transport.host, transport.port

        async def one_client() -> None:
            reader, writer = await asyncio.open_connection(host, port)
            try:
                await write_frame(writer, {"kind": "ping"})
                await read_frame(reader)
            finally:
                writer.close()
                await writer.wait_closed()

        clients = [asyncio.create_task(one_client()) for _ in range(6)]
        # Give the accept loop time to admit up to the cap and block the rest.
        for _ in range(50):
            await asyncio.sleep(0)
            if peak >= 2 and live >= 2:
                break
        # The cap held: at most 2 handlers ran concurrently even with 6 clients.
        assert peak == 2
        assert live <= 2

        gate.set()
        await asyncio.gather(*clients)
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_slow_loris_frame_is_dropped_at_read_deadline():
    """A peer that never completes its frame is dropped, freeing its slot."""

    async def run() -> None:
        handled = 0

        async def handler(request: dict) -> dict:
            nonlocal handled
            handled += 1
            return {"ok": True}

        # 1s deadline keeps the test fast; it is an integer policy knob.
        transport = TcpTransport(host="127.0.0.1", port=0, read_timeout_s=1)
        await transport.listen(handler)

        reader, writer = await asyncio.open_connection(
            transport.host, transport.port
        )
        try:
            # Send a length prefix promising 64 bytes but never send the body:
            # a classic slow-loris. The server must time out the read.
            writer.write((64).to_bytes(4, "big"))
            await writer.drain()
            # The server closes our end once its read deadline fires.
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()

        # The handler was never invoked on the incomplete frame.
        assert handled == 0
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_slot_is_released_after_each_connection():
    """Serving one connection returns its slot so later peers are served."""

    async def run() -> None:
        async def handler(request: dict) -> dict:
            return {"echo": request.get("n")}

        transport = TcpTransport(host="127.0.0.1", port=0, max_inbound=1)
        await transport.listen(handler)
        host, port = transport.host, transport.port

        # Serially open more connections than the cap; each must complete,
        # proving the semaphore slot is released after every connection.
        for n in range(5):
            reader, writer = await asyncio.open_connection(host, port)
            try:
                await write_frame(writer, {"kind": "ping", "n": n})
                resp = await read_frame(reader)
                assert resp == {"echo": n}
            finally:
                writer.close()
                await writer.wait_closed()

        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


def test_one_frame_per_connection():
    """Only a single request frame is served per accepted socket.

    A peer cannot pipeline a flood of frames down one connection to slip past
    the connection cap: the second frame is never read by the handler.
    """

    async def run() -> None:
        seen = []

        async def handler(request: dict) -> dict:
            seen.append(request.get("n"))
            return {"ok": True}

        transport = TcpTransport(host="127.0.0.1", port=0)
        await transport.listen(handler)

        reader, writer = await asyncio.open_connection(
            transport.host, transport.port
        )
        try:
            await write_frame(writer, {"kind": "ping", "n": 1})
            # Pipeline a second frame the server should ignore on this socket.
            await write_frame(writer, {"kind": "ping", "n": 2})
            await read_frame(reader)
            # Server closes after the single response.
            assert await reader.read() == b""
        finally:
            writer.close()
            await writer.wait_closed()

        assert seen == [1]
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))
