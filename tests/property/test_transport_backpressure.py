"""Backpressure / DoS guards on the TCP carrier (gap c).

``TcpTransport.listen`` must not let a connection flood or a slow-loris peer
exhaust the node. These tests pin the carrier-level guards added to the accept
loop, all of which are deterministic integer policy knobs that never touch the
wire framing bytes (so signed-record byte-identity is unaffected — that gate
lives in ``test_transport_roundtrip.py``):

  * a bounded concurrent-connection semaphore caps how many inbound connections
    are *served* at once;
  * a per-connection single-frame read deadline drops a peer that stalls
    mid-frame, freeing its slot;
  * an accept-queue deadline drops a connection that parks too long waiting for a
    serving slot (#173), bounding parked fds by (arrival_rate x timeout);
  * a hard ``max_open_conns`` ceiling on concurrently-OPEN inbound sockets,
    checked at accept BEFORE parking (#174), which caps the live fd count to a
    constant even for a burst that all arrives within the accept-queue window —
    the residual that the timeout alone misses. An exact integer open-counter
    tracks held fds and must return to 0 once a burst fully drains.
"""

import asyncio

import pytest

from knitweb.p2p.transport import (
    DEFAULT_ACCEPT_QUEUE_TIMEOUT_S,
    DEFAULT_MAX_INBOUND,
    DEFAULT_MAX_OPEN_CONNS,
    DEFAULT_READ_TIMEOUT_S,
    TcpTransport,
)
from knitweb.p2p.wire import read_frame, write_frame


@pytest.mark.property
def test_default_limits_are_positive_integers():
    # Deterministic integer knobs, no randomness / wall-clock in the defaults.
    assert isinstance(DEFAULT_MAX_INBOUND, int) and DEFAULT_MAX_INBOUND >= 1
    assert isinstance(DEFAULT_READ_TIMEOUT_S, int) and DEFAULT_READ_TIMEOUT_S >= 1
    # #168: the open-connection ceiling must leave room for every serving slot.
    assert isinstance(DEFAULT_MAX_OPEN_CONNS, int)
    assert DEFAULT_MAX_OPEN_CONNS >= DEFAULT_MAX_INBOUND
    assert isinstance(DEFAULT_ACCEPT_QUEUE_TIMEOUT_S, int)
    assert DEFAULT_ACCEPT_QUEUE_TIMEOUT_S >= 1


@pytest.mark.property
@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_caps_rejected(bad):
    with pytest.raises(ValueError):
        TcpTransport(max_inbound=bad)
    with pytest.raises(ValueError):
        TcpTransport(read_timeout_s=bad)
    with pytest.raises(ValueError):
        TcpTransport(accept_queue_timeout_s=bad)


@pytest.mark.property
def test_open_ceiling_below_serving_cap_rejected():
    # The open ceiling must be >= the serving cap, else served connections would be
    # rejected before they could ever acquire a slot (#168).
    with pytest.raises(ValueError):
        TcpTransport(max_inbound=64, max_open_conns=63)


def test_open_connection_ceiling_drops_excess_immediately():
    """#168: beyond ``max_open_conns`` OPEN sockets, the newest is dropped at once.

    The ``max_inbound`` semaphore bounds only SERVED connections; without the open
    ceiling a flood of idle sockets parked on the slot would pin the fd table. Here
    one held handler occupies the single slot, a second connection parks, and the
    third — over the ceiling — is closed immediately without being served.
    """

    async def run() -> None:
        live = 0
        peak = 0
        gate = asyncio.Event()

        async def handler(request: dict) -> dict:
            nonlocal live, peak
            live += 1
            peak = max(peak, live)
            await gate.wait()
            live -= 1
            return {"ok": True}

        # 1 served slot, ceiling of 2 open sockets, long park timeout so the parked
        # connection stays open (does not time out) during the assertions.
        transport = TcpTransport(
            host="127.0.0.1",
            port=0,
            max_inbound=1,
            max_open_conns=2,
            accept_queue_timeout_s=30,
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        # conn1 takes the only slot; handler is held by the gate.
        r1, w1 = await asyncio.open_connection(host, port)
        await write_frame(w1, {"kind": "ping"})
        for _ in range(200):
            await asyncio.sleep(0)
            if peak >= 1:
                break
        assert peak == 1

        # conn2 is accepted (open) but parks on the busy slot — open_conns hits the
        # ceiling. Poll the real counter so the next step is race-free.
        r2, w2 = await asyncio.open_connection(host, port)
        await write_frame(w2, {"kind": "ping"})
        for _ in range(200):
            await asyncio.sleep(0)
            if transport._open_conns >= 2:
                break
        assert transport._open_conns == 2

        # conn3 is over the ceiling → server drops it immediately (EOF), no serve.
        r3, w3 = await asyncio.open_connection(host, port)
        assert await asyncio.wait_for(r3.read(), timeout=5) == b""
        assert peak == 1  # the handler still ran only once

        # Releasing conn1 frees the slot; the parked conn2 is then served.
        gate.set()
        assert await read_frame(r1) == {"ok": True}
        assert await read_frame(r2) == {"ok": True}
        for w in (w1, w2, w3):
            w.close()
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_parked_connection_dropped_at_accept_queue_deadline():
    """#168: a connection parked waiting for a slot is dropped at the queue deadline.

    Even under the open ceiling, a socket cannot pin its fd forever before doing
    work: the acquire is bounded by ``accept_queue_timeout_s``.
    """

    async def run() -> None:
        served = 0
        gate = asyncio.Event()

        async def handler(request: dict) -> dict:
            nonlocal served
            served += 1
            await gate.wait()
            return {"ok": True}

        # 1 slot, roomy open ceiling, short 1s park deadline.
        transport = TcpTransport(
            host="127.0.0.1",
            port=0,
            max_inbound=1,
            max_open_conns=10,
            accept_queue_timeout_s=1,
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        # conn1 takes and holds the only slot.
        r1, w1 = await asyncio.open_connection(host, port)
        await write_frame(w1, {"kind": "ping"})
        for _ in range(200):
            await asyncio.sleep(0)
            if served >= 1:
                break
        assert served == 1

        # conn2 parks; after the 1s accept-queue deadline the server drops it (EOF).
        r2, w2 = await asyncio.open_connection(host, port)
        await write_frame(w2, {"kind": "ping"})
        assert await asyncio.wait_for(r2.read(), timeout=5) == b""
        assert served == 1  # conn2 never reached the handler

        gate.set()
        assert await read_frame(r1) == {"ok": True}
        w1.close()
        w2.close()
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=15))


def test_burst_within_window_never_exceeds_open_ceiling():
    """#174: a burst that all arrives WITHIN the accept-queue window is hard-capped.

    This pins the residual that #173's ``accept_queue_timeout_s`` ALONE misses.
    That timeout bounds parked fds only by ``(arrival_rate x timeout)``: every
    connection in a fast-enough burst parks and pins an fd for the WHOLE window
    before any deadline fires, so a high arrival rate transiently exhausts the fd
    table. Here the park deadline is set large (30s) so it never fires during the
    test; the only thing that can bound the live open-count is the
    ``max_open_conns`` ceiling, checked at accept BEFORE the connection parks.

    With ``max_inbound=2`` (both slots pinned by a gate) and ``max_open_conns=4``,
    a burst of K=20 connections that connect but never send must leave at most 4
    server-side sockets concurrently open at every instant, and the 16 excess
    connections must get a PROMPT EOF (closed at accept, not a 30s park).
    """

    K = 20
    CEILING = 4

    async def run() -> None:
        gate = asyncio.Event()

        async def handler(request: dict) -> dict:
            # Two handlers acquire the only two serving slots and hold them, so
            # every later connection must park on the semaphore — exactly the
            # state in which #173-alone would let the burst pile up unbounded.
            await gate.wait()
            return {"ok": True}

        transport = TcpTransport(
            host="127.0.0.1",
            port=0,
            max_inbound=2,
            max_open_conns=CEILING,
            # Large enough that NO park deadline fires during the burst: the
            # ceiling, not the timeout, is what must hold the bound.
            accept_queue_timeout_s=30,
            # Short read deadline so that, once the gate releases and the parked
            # burst sockets win a slot, they drain promptly (they never sent a
            # frame) rather than pinning the counter for the default 30s.
            read_timeout_s=1,
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        writers: list[asyncio.StreamWriter] = []
        # Pin both serving slots: two connections that send a frame and whose
        # handlers block on the gate, so the slots stay busy for the whole burst.
        for _ in range(2):
            r, w = await asyncio.open_connection(host, port)
            await write_frame(w, {"kind": "ping"})
            writers.append(w)
        # Let the accept loop admit both and seat them in handlers.
        for _ in range(500):
            await asyncio.sleep(0)
            if transport._open_conns >= 2:
                break
        assert transport._open_conns == 2

        # The burst: K >> ceiling connections that connect but never send a frame,
        # opened back-to-back (all within the 30s window). Track the peak open-count
        # the server ever reaches while admitting them.
        peak_open = transport._open_conns
        readers: list[asyncio.StreamReader] = []
        for _ in range(K):
            r, w = await asyncio.open_connection(host, port)
            readers.append(r)
            writers.append(w)
            # Pump the loop so the server processes the accept and updates the
            # counter, then sample the live open-count.
            for _ in range(20):
                await asyncio.sleep(0)
                peak_open = max(peak_open, transport._open_conns)
            # INVARIANT: the live open-count never crosses the hard ceiling, at
            # every step of the burst — the point #173-alone misses.
            assert transport._open_conns <= CEILING

        assert peak_open <= CEILING

        # Every client REFUSED at the ceiling must receive a PROMPT EOF — closed at
        # accept, never parked for 30s. The 2 pinned slots already consume 2 of the
        # CEILING open budget, so only (CEILING - 2) of the burst connections are
        # admitted (and park, staying open); the remaining K - (CEILING - 2) burst
        # connections are refused at accept and get an immediate EOF. We read each
        # with a tight 1s deadline << the 30s park timeout: a refused connection
        # that was instead parked (the #173-alone bug) would hang past 1s. The few
        # legitimately-parked ones under the ceiling may not EOF yet, so we count
        # prompt EOFs rather than requiring all.
        admitted_from_burst = CEILING - 2
        prompt_eofs = 0
        for r in readers:
            try:
                if await asyncio.wait_for(r.read(), timeout=1) == b"":
                    prompt_eofs += 1
            except asyncio.TimeoutError:
                # A still-open (parked) burst connection under the ceiling.
                pass
        # Exactly the over-ceiling excess was shed at accept (prompt EOF); only the
        # (CEILING - 2) admitted-and-parked burst sockets did not EOF.
        assert prompt_eofs == K - admitted_from_burst

        # Release the gate so the two seated handlers finish, then close.
        gate.set()
        for w in writers:
            w.close()
        await transport.close()

        # COUNTER EXACTNESS: after the burst fully drains the open-counter must
        # return to 0 — no leak/double-count on any exit path would otherwise wedge
        # the transport into a permanent false-full. A short bounded poll (not a
        # fixed race-sleep) lets the close handshakes for the open sockets finish.
        for _ in range(200):
            if transport._open_conns == 0:
                break
            await asyncio.sleep(0.01)
        assert transport._open_conns == 0

    asyncio.run(asyncio.wait_for(run(), timeout=20))


def test_open_counter_drains_to_zero_after_mixed_burst():
    """#174 counter-exactness: every exit path returns the open-counter to 0.

    Exercises all four exit paths in one run — immediate-reject (over ceiling),
    timeout-close (parked past a short deadline), served-then-released (a normal
    round-trip), and a clean close — then asserts ``_open_conns`` is exactly 0. A
    leaked or double-counted fd would leave it non-zero and eventually wedge the
    transport at a false-full ceiling.
    """

    async def run() -> None:
        served = 0

        async def handler(request: dict) -> dict:
            nonlocal served
            served += 1
            return {"echo": request.get("n")}

        transport = TcpTransport(
            host="127.0.0.1",
            port=0,
            max_inbound=1,
            max_open_conns=2,
            accept_queue_timeout_s=1,
            # Short read deadline so a connection that wins the slot but never sends
            # a frame drops promptly at the read path (1s) rather than the default
            # 30s, keeping the counter-drain assertion fast and deterministic.
            read_timeout_s=1,
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        # Path A: a normal client round-trips (served-then-released).
        r, w = await asyncio.open_connection(host, port)
        await write_frame(w, {"kind": "ping", "n": 7})
        assert await read_frame(r) == {"echo": 7}
        w.close()
        await w.wait_closed()

        # Paths B/C/D: open a burst of idle connections > ceiling. Some are refused
        # immediately at accept (over ceiling), some park then time out at the 1s
        # accept-queue deadline, some win the slot then drop at the 1s read deadline
        # (they never send a frame). Every one of them must ultimately drain.
        idle: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []
        for _ in range(8):
            r2, w2 = await asyncio.open_connection(host, port)
            idle.append((r2, w2))
        # Drain every idle connection to EOF concurrently (reading in open-order
        # would serialize behind whichever connection currently holds the 1s slot;
        # gathering lets all deadlines elapse in parallel). Each is refused at
        # accept, dropped at the 1s park deadline, or dropped at the 1s read
        # deadline — all bounded, so a 10s cap is generous.
        async def drain(r2: asyncio.StreamReader) -> bytes:
            return await asyncio.wait_for(r2.read(), timeout=15)

        results = await asyncio.gather(*(drain(r2) for r2, _w in idle))
        assert all(chunk == b"" for chunk in results)
        for _r, w2 in idle:
            w2.close()

        for _ in range(200):
            if transport._open_conns == 0:
                break
            await asyncio.sleep(0.01)
        assert transport._open_conns == 0
        assert served == 1  # only the well-behaved client reached the handler
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=30))


def test_well_behaved_client_round_trips_under_ceiling():
    """#174 legit preserved: an honest peer below the ceiling is served as before.

    With open-count < ``max_open_conns`` a client that connects and sends promptly
    round-trips its frame exactly as without the ceiling — the guard only ever
    sheds connections strictly above the ceiling.
    """

    async def run() -> None:
        async def handler(request: dict) -> dict:
            return {"echo": request.get("n")}

        transport = TcpTransport(
            host="127.0.0.1", port=0, max_inbound=4, max_open_conns=16
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        for n in range(5):
            r, w = await asyncio.open_connection(host, port)
            try:
                await write_frame(w, {"kind": "ping", "n": n})
                assert await read_frame(r) == {"echo": n}
            finally:
                w.close()
                await w.wait_closed()

        assert transport._open_conns == 0
        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


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
