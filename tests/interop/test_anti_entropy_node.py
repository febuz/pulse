"""Self-healing anti-entropy wired into the live nodes (issue #44).

The standalone driver (``p2p/anti_entropy.py``, #43) is socket-free and proven in
``tests/property/test_anti_entropy.py``. This suite proves it actually *runs* once
wired into a live node: two real in-process nodes converge, a peer drops, the
weaver keeps weaving, the peer rejoins, and the background loop reconnects +
re-syncs so the rejoined peer converges back onto the weaver's ``state_root``.

The convergence loop's clock is injected with a virtual clock that elapses **no
real time** but yields to the event loop, so a churn scenario is deterministic and
fast — no ``sleep`` races, no flakiness.
"""

import asyncio
import socket

import pytest

from knitweb.fabric.feed import Feed
from knitweb.fabric.node import FabricNode
from knitweb.p2p import AsyncioP2PNode


def _free_port() -> int:
    """Reserve a concrete free TCP port so a node can stop and restart on it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(coro):
    return asyncio.run(coro)


class VirtualClock:
    """Injected sleep: elapses an integer virtual clock, no real time.

    ``await sleep(delay)`` records the delay and yields once to the event loop
    (``asyncio.sleep(0)``) so background socket I/O the anti-entropy round kicked
    off gets a chance to run — but zero real wall-clock time passes, keeping the
    test fast and deterministic.
    """

    def __init__(self) -> None:
        self.now = 0
        self.delays: list[int] = []

    async def sleep(self, delay: int) -> None:
        assert isinstance(delay, int) and not isinstance(delay, bool)
        assert delay >= 0
        self.delays.append(delay)
        self.now += delay
        await asyncio.sleep(0)


async def _settle(predicate, *, limit: int = 200) -> bool:
    """Yield to the loop until ``predicate()`` holds (bounded), no real sleeps."""
    for _ in range(limit):
        if predicate():
            return True
        await asyncio.sleep(0)
    return predicate()


@pytest.mark.interop
def test_anti_entropy_off_by_default_does_not_change_serve():
    async def scenario():
        a = FabricNode()
        async with a:
            # A plain start() must not launch any background convergence task.
            assert a._anti_entropy_task is None
            await a.weave({"kind": "knowledge", "title": "x", "body": "y", "author": a.pub})
            assert a._anti_entropy_task is None

    run(scenario())


@pytest.mark.interop
def test_dropped_peer_rejoins_and_reconverges_via_anti_entropy():
    async def scenario():
        clock = VirtualClock()
        a = FabricNode()  # the weaver / source of truth
        b = FabricNode()  # the healer: re-syncs from a on a loop

        async with a:
            # Records woven before b exists — a is ahead of b from the start.
            await a.weave({"kind": "knowledge", "title": "t0", "body": "0", "author": a.pub})
            await a.weave({"kind": "knowledge", "title": "t1", "body": "1", "author": a.pub})

            # b boots and starts its self-healing loop, pointed at a.
            await b.start()
            assert b.state_root != a.state_root  # diverged
            b.start_anti_entropy([a.address], interval=1, sleep=clock.sleep)

            # The loop re-syncs and b converges onto a's root.
            assert await _settle(lambda: b.state_root == a.state_root)
            assert b.web.size == a.web.size == (2, 0)

            # --- peer B "drops": a stays up and keeps weaving while b is gone ---
            await b.stop()  # also cancels b's loop
            await a.weave({"kind": "knowledge", "title": "t2", "body": "2", "author": a.pub})
            await a.weave({"kind": "knowledge", "title": "t3", "body": "3", "author": a.pub})
            assert a.web.size == (4, 0)

            # While b is down its loop must not have crashed anything: confirm the
            # task is finished (cancelled) rather than raised.
            assert b._anti_entropy_task is None

            # --- peer B rejoins: restart it and its loop; it must re-converge ---
            b2 = FabricNode(priv=b._priv)  # same identity, fresh listener
            async with b2:
                # b2 starts behind (its Web only has what the old b had: 2 records
                # would require carrying state; here it boots empty to prove the
                # loop drives a *full* re-sync from scratch on reconnect).
                assert b2.state_root != a.state_root
                b2.start_anti_entropy([a.address], interval=1, sleep=clock.sleep)

                # Reconnect + re-sync → b2 converges to a's *current* (post-churn)
                # state_root, including the records woven while it was away.
                assert await _settle(lambda: b2.state_root == a.state_root)
                assert b2.web.size == (4, 0)

                # The driver swallowed nothing fatal: the loop is still alive.
                assert not b2._anti_entropy_task.done()

    run(scenario())


@pytest.mark.interop
def test_loop_survives_a_refusing_peer_then_heals_on_reconnect():
    async def scenario():
        clock = VirtualClock()
        port = _free_port()  # a concrete endpoint a can later bind
        a = FabricNode(port=port)
        b = FabricNode()
        a_addr = a.address  # b's loop dials this even while a is down

        async with b:
            # Point b's loop at a peer that is NOT listening yet: every round
            # raises (connection refused). The loop must keep running, not crash.
            b.start_anti_entropy([a_addr], interval=1, sleep=clock.sleep)

            # Let several cycles run against the dead peer.
            for _ in range(20):
                await asyncio.sleep(0)
            assert not b._anti_entropy_task.done()  # survived the refusals
            assert b.web.size == (0, 0)

            # Now a comes up on the same port and weaves; b's loop reconnects
            # and converges without b's loop ever being restarted.
            async with a:
                assert a.address == a_addr  # same endpoint b has been dialing
                await a.weave({"kind": "knowledge", "title": "late", "body": "L", "author": a.pub})
                assert await _settle(lambda: b.state_root == a.state_root)
                assert b.web.size == (1, 0)
                assert not b._anti_entropy_task.done()

    run(scenario())


@pytest.mark.interop
def test_asyncio_node_feed_resync_heals_after_server_drop():
    """The AsyncioP2PNode wiring: a feed-sync loop re-pulls after a server drop."""
    async def scenario():
        clock = VirtualClock()
        port = _free_port()
        feed = Feed.create()
        feed.append({"kind": "knowledge", "title": "alpha"})
        feed.append({"kind": "knowledge", "title": "beta"})

        server = AsyncioP2PNode(port=port)
        server.add_feed(feed)
        srv_addr = server.address

        client = AsyncioP2PNode()
        async with client:
            # Client's loop re-bootstraps + re-pulls `feed` from the server.
            client.start_anti_entropy(
                [srv_addr], feeds=[feed.feed], interval=1, sleep=clock.sleep
            )
            async with server:
                assert server.address == srv_addr
                assert await _settle(
                    lambda: client.replicas.get(feed.feed) is not None
                    and client.replicas[feed.feed].head.length == 2
                )

            # --- server drops, then grows the feed while the client is cut off ---
            feed.append({"kind": "knowledge", "title": "gamma"})
            server.add_feed(feed)

            # Loop keeps running against the now-dead server (rounds raise; the
            # driver swallows them), so the task must still be alive.
            for _ in range(10):
                await asyncio.sleep(0)
            assert not client._anti_entropy_task.done()
            assert client.replicas[feed.feed].head.length == 2  # still stale

            # --- server rejoins on the same port: client re-syncs and converges ---
            async with server:
                assert await _settle(
                    lambda: client.replicas[feed.feed].head.length == 3
                )
                assert not client._anti_entropy_task.done()

    run(scenario())
