"""Pre-slot fd-pinning guard on the TCP carrier (issue #168).

``TcpTransport.listen`` spawns one ``_accept`` coroutine per inbound socket.
Each coroutine holds the open fd + StreamReader the instant it runs, then waits
on the ``max_inbound`` semaphore. Without a deadline on that wait, a peer that
opens many idle sockets and never sends a frame parks one fd-holding coroutine
per socket *before* the cap — so the number of concurrently-open inbound
connections is unbounded by ``max_inbound`` and the fd table is exhausted.

These tests pin the carrier-level guard: a pre-slot acquire deadline
(``accept_queue_timeout_s``) that closes a parked connection cleanly so the
count of concurrently-open inbound sockets is bounded. They are deterministic
integer policy knobs and never touch the wire framing bytes (byte-identity of
signed records is unaffected — that gate lives elsewhere).
"""

import asyncio

import pytest

from knitweb.p2p.transport import TcpTransport
from knitweb.p2p.wire import read_frame, write_frame


async def _saturate_served_slots(host, port, max_inbound, hold_gate):
    """Open ``max_inbound`` clients that each send a frame and occupy a served
    slot until ``hold_gate`` is set, draining the semaphore to zero."""

    async def occupy() -> None:
        reader, writer = await asyncio.open_connection(host, port)
        await write_frame(writer, {"kind": "ping"})
        # Hold the connection: the handler blocks on hold_gate, pinning the slot.
        await hold_gate.wait()
        try:
            await read_frame(reader)
        except Exception:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

    tasks = [asyncio.create_task(occupy()) for _ in range(max_inbound)]
    return tasks


@pytest.mark.property
def test_idle_preslot_connections_are_bounded():
    """Idle sockets opened past the cap must NOT park forever holding an fd.

    PRE-FIX (bare ``await acquire()``): the extra idle connections are accepted
    and never closed — the server holds K >> max_inbound open inbound sockets,
    each pinning an fd. The test detects the gap by asserting the excess idle
    connections are eventually closed by the server (EOF). With the unbounded
    accept loop no EOF ever arrives and the test fails (times out waiting).
    """

    MAX_INBOUND = 2
    EXTRA = 8  # K >> max_inbound idle pre-slot connections
    # Integer-second pre-slot deadline kept small so the test is fast.
    ACCEPT_TIMEOUT = 1

    async def run() -> None:
        hold_gate = asyncio.Event()

        async def handler(request: dict) -> dict:
            # Served handlers block, keeping every slot occupied so the EXTRA
            # connections are forced to park on the pre-slot acquire.
            await hold_gate.wait()
            return {"ok": True}

        transport = TcpTransport(
            host="127.0.0.1",
            port=0,
            max_inbound=MAX_INBOUND,
            accept_queue_timeout_s=ACCEPT_TIMEOUT,
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        # 1) Saturate every served slot so the semaphore is fully drained.
        served = await _saturate_served_slots(host, port, MAX_INBOUND, hold_gate)
        # Let the server admit and start serving the saturating clients.
        for _ in range(200):
            await asyncio.sleep(0)
            if transport._inbound._value == 0:
                break
        assert transport._inbound._value == 0, "served slots not saturated"

        # 2) Open EXTRA idle connections that connect but never send a frame.
        #    Each parks one fd-holding _accept coroutine before the semaphore.
        idle = []
        for _ in range(EXTRA):
            r, w = await asyncio.open_connection(host, port)
            idle.append((r, w))

        # 3) The carrier must bound parked connections: each idle pre-slot
        #    connection is closed at the acquire deadline. PRE-FIX this never
        #    happens, so waiting for EOF on all of them hangs -> outer timeout.
        async def expect_eof(reader) -> None:
            data = await reader.read()
            assert data == b"", "idle parked connection should be closed by server"

        await asyncio.wait_for(
            asyncio.gather(*(expect_eof(r) for r, _ in idle)),
            # Generous relative to ACCEPT_TIMEOUT but far below the outer cap;
            # PRE-FIX the bare acquire() never closes these, so this elapses.
            timeout=ACCEPT_TIMEOUT + 3,
        )

        # Cleanup.
        for _, w in idle:
            w.close()
            try:
                await w.wait_closed()
            except Exception:
                pass
        hold_gate.set()
        for t in served:
            t.cancel()
        await asyncio.gather(*served, return_exceptions=True)
        await transport.close()

    # Outer cap well above ACCEPT_TIMEOUT so a real timeout-close passes fast,
    # while a never-closing (pre-fix) parked connection trips it as a failure.
    asyncio.run(asyncio.wait_for(run(), timeout=15))


@pytest.mark.property
def test_well_behaved_client_still_round_trips():
    """A normal peer that sends a frame promptly is served exactly as before."""

    async def run() -> None:
        async def handler(request: dict) -> dict:
            return {"echo": request.get("n")}

        # Generous integer acquire deadline: an honest peer is never closed.
        transport = TcpTransport(
            host="127.0.0.1", port=0, max_inbound=2, accept_queue_timeout_s=30
        )
        await transport.listen(handler)
        host, port = transport.host, transport.port

        reader, writer = await asyncio.open_connection(host, port)
        try:
            await write_frame(writer, {"kind": "ping", "n": 7})
            resp = await read_frame(reader)
            assert resp == {"echo": 7}
        finally:
            writer.close()
            await writer.wait_closed()

        await transport.close()

    asyncio.run(asyncio.wait_for(run(), timeout=10))


@pytest.mark.property
def test_accept_queue_timeout_default_is_positive_integer():
    from knitweb.p2p.transport import DEFAULT_ACCEPT_QUEUE_TIMEOUT_S

    assert (
        isinstance(DEFAULT_ACCEPT_QUEUE_TIMEOUT_S, int)
        and DEFAULT_ACCEPT_QUEUE_TIMEOUT_S >= 1
    )


@pytest.mark.property
@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_accept_queue_timeout_rejected(bad):
    with pytest.raises(ValueError):
        TcpTransport(accept_queue_timeout_s=bad)
