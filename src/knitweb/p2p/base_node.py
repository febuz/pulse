"""Shared carrier+reputation+metric scaffolding for the two node stacks (#23).

Both :class:`knitweb.p2p.node.AsyncioP2PNode` and
:class:`knitweb.fabric.node.FabricNode` independently grew the *same*
carrier-agnostic machinery: they construct a listening :class:`Transport` plus a
routing :class:`Dialer`, expose the same ``address``/``host``/``port`` accessors
and ``add_transport``, run the same ``start``/``stop`` lifecycle wiring the
listener to ``self._dispatch``, drive the same opt-in anti-entropy loop, hold a
:class:`PeerReputation` and a :class:`Metrics` bag, and gate every connection
through the same banned-peer + malformed/oversized-frame logic.

``BaseNode`` extracts exactly that shared part — and nothing that touches a
signed/canonical/hash path. Each subclass keeps its own payload handlers, its
own routing table (``_route``), its caught-exception set (``_dispatch_errors``),
its banned-branch ``frames_out`` policy (``_count_frames_out_on_banned``), and
its post-prologue connection body (``_serve_connection``). This is a pure
refactor: the wire bytes, the dispatch semantics, the ban thresholds, and the
metric names are all unchanged, so a synced Knit's CID is byte-identical.
"""

from __future__ import annotations

import asyncio

from .anti_entropy import AntiEntropy, Backoff
from .metrics import Metrics
from .relay import ENVELOPE_PEER_KEY
from .reputation import Offense, PeerReputation
from .transport import Dialer, PeerAddress, TcpTransport, Transport
from .wire import WireError, read_frame, write_frame

__all__ = ["BaseNode"]


class BaseNode:
    """The carrier-agnostic half both node stacks share verbatim.

    Owns only the five shared instance fields (``transport``, ``dialer``,
    ``reputation``, ``metrics``, plus the ``_listening`` / ``_anti_entropy_task``
    handles); a subclass calls ``super().__init__(...)`` first and then sets its
    own (account/feeds/web/keypair/…) state. The polymorphic seams a subclass
    must supply are ``_route``, ``_dispatch_errors``,
    ``_count_frames_out_on_banned``, ``_anti_entropy_rounds`` and
    ``_serve_connection``.
    """

    # Subclass policy hooks (defaults documented per-subclass override).
    _dispatch_errors: tuple = (WireError, ValueError)
    _count_frames_out_on_banned: bool = True

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        transport: Transport | None = None,
        extra_transports: list[Transport] | None = None,
    ) -> None:
        # The listening transport (TCP by default; pass a RelayTransport to be
        # reachable from behind NAT). Outbound dials are routed by the Dialer
        # according to each PeerAddress's transport tag, so a node can hold a mix
        # of tcp:// and relay:// peers at once.
        self.transport: Transport = transport or TcpTransport(host=host, port=port)
        self.dialer = Dialer()
        for tr in [self.transport, *(extra_transports or [])]:
            self.dialer.register(tr)
        # The Byzantine-consequence ledger this node owns: detected/proven
        # misbehavior is funnelled here, and the per-connection _handle_peer
        # wrapper refuses banned peers before _dispatch ever sees a request.
        self.reputation = PeerReputation()
        # Integer-only observability over the wire path (frames in/out,
        # malformed/oversized frames, banned-peer refusals, …). Node-local
        # bookkeeping only: it touches no signed record and no hash path, so a
        # synced Knit's CID is byte-identical whether or not this node is metered.
        self.metrics = Metrics()
        self._listening = False
        # Opt-in self-healing convergence loop (issue #44). Off by default — the
        # handle lets stop() cancel the loop cleanly.
        self._anti_entropy_task: "asyncio.Task | None" = None

    # -- server lifecycle -------------------------------------------------

    @property
    def address(self) -> PeerAddress:
        return self.transport.local_address()

    @property
    def host(self) -> str:
        return self.transport.local_address().host

    @property
    def port(self) -> int:
        return self.transport.local_address().port

    def add_transport(self, transport: Transport) -> None:
        """Register an extra outbound transport (e.g. relay:// dialing)."""
        self.dialer.register(transport)

    async def start(self) -> None:
        """Start listening for one-request-per-connection peer calls."""
        if self._listening:
            return
        await self.transport.listen(self._dispatch)
        self._listening = True

    async def stop(self) -> None:
        """Stop the listener (and any running anti-entropy loop)."""
        await self.stop_anti_entropy()
        if not self._listening:
            return
        await self.transport.close()
        self._listening = False

    # -- self-healing anti-entropy (issue #44) ----------------------------

    def _spawn_anti_entropy(
        self,
        rounds,
        *,
        interval: int,
        ceiling: int,
        sleep,
    ) -> "asyncio.Task":
        """Build + launch the anti-entropy driver from a subclass-built ``rounds``.

        The shared body of each subclass's ``start_anti_entropy``. Kept here so
        both nodes preserve their own public ``start_anti_entropy`` *signature*
        (the asyncio one carries a ``feeds`` kwarg the fabric one does not) while
        sharing the identical idempotency guard + driver-spawn body.
        """
        if self._anti_entropy_task is not None and not self._anti_entropy_task.done():
            return self._anti_entropy_task
        driver = AntiEntropy(
            rounds,
            sleep=sleep or self._anti_entropy_sleep,
            backoff=Backoff(base=interval, ceiling=ceiling),
        )
        self._anti_entropy = driver
        self._anti_entropy_task = asyncio.ensure_future(self._anti_entropy_run(driver))
        return self._anti_entropy_task

    async def stop_anti_entropy(self) -> None:
        """Cancel the background anti-entropy loop if one is running."""
        task = self._anti_entropy_task
        self._anti_entropy_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _anti_entropy_sleep(delay: int) -> None:
        # The prod clock: a seconds-based asyncio sleep. Tests inject a virtual
        # clock by driving the AntiEntropy driver directly instead.
        await asyncio.sleep(delay)

    async def _anti_entropy_run(self, driver: AntiEntropy) -> None:
        # Drive cycles forever (until cancelled). The driver already swallows a
        # failed round, so a dropped peer only backs the schedule off.
        try:
            while True:
                await driver.run_cycle()
        except asyncio.CancelledError:
            raise

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, _exc_type, _exc, _tb) -> None:
        await self.stop()

    # -- dispatch ----------------------------------------------------------

    async def _dispatch(self, msg: dict) -> dict:
        """Transport-agnostic request handler: request map in, response map out.

        The handler the listening :class:`Transport` feeds every decoded request
        to (TCP accept loop or relay mailbox poll alike). The TCP stream applies
        its banned-peer gate and frame-level penalties in :meth:`_handle_peer`
        (concerns the carrier owns before a request is ever decoded). The relay
        carrier has no socket, so it stamps the sender's identity onto the request
        as a transport-envelope key (:data:`ENVELOPE_PEER_KEY`); here we honour the
        *same* ban gate before any work, then drop the key so it never reaches
        signed/business logic.

        Routing, the caught-exception set, and the banned-branch ``frames_out``
        policy are subclass seams (``_route`` / ``_dispatch_errors`` /
        ``_count_frames_out_on_banned``), so each node keeps its exact behavior.
        """
        self.metrics.incr("frames_in")
        peer_id = msg.pop(ENVELOPE_PEER_KEY, None)
        if isinstance(peer_id, str) and self.reputation.is_banned(peer_id):
            self.metrics.incr("banned_refusals")
            if self._count_frames_out_on_banned:
                self.metrics.incr("frames_out")
            return self._error("banned", "peer is banned")
        try:
            out = self._route(msg.get("kind"), msg)
        except self._dispatch_errors as exc:
            out = self._error("bad-request", str(exc))
        self.metrics.incr("frames_out")
        return out

    def _route(self, kind, msg: dict) -> dict:
        """Subclass routing table: kind -> handler. Raises an unknown-kind error."""
        raise NotImplementedError

    # -- per-connection TCP wrapper ---------------------------------------

    @staticmethod
    def _peer_id(writer: asyncio.StreamWriter) -> str:
        """A stable reputation key for the connected peer (its remote endpoint)."""
        peername = writer.get_extra_info("peername")
        if isinstance(peername, tuple) and len(peername) >= 2:
            return f"{peername[0]}:{peername[1]}"
        return str(peername)

    async def _handle_peer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Per-connection reputation wrapper over a single TCP stream.

        The shared prologue — banned-peer gate, then read_frame with the
        malformed/oversized penalty + matching frame-fault counter — is identical
        across both nodes and lives here. The post-read body (routing + the
        node-specific error tail) is the :meth:`_serve_connection` seam each
        subclass overrides verbatim. The ``finally`` socket teardown is shared.
        """
        peer_id = self._peer_id(writer)
        try:
            # Reputation gate: a banned peer (a proven equivocator, an accumulated
            # bad-proof seeder, …) is refused and disconnected before any work.
            if self.reputation.is_banned(peer_id):
                # Refused before a frame is ever decoded — a carrier-owned ban
                # gate, so it counts the refusal but no frames_in/out.
                self.metrics.incr("banned_refusals")
                await write_frame(writer, self._error("banned", "peer is banned"))
                return
            try:
                msg = await read_frame(reader)
            except WireError as exc:
                # Malformed or oversized wire frame → graded misbehavior points,
                # and the matching frame-fault counter the carrier owns.
                oversized = "too large" in str(exc)
                self.metrics.incr(
                    "frames_oversized" if oversized else "frames_malformed"
                )
                offense = (
                    Offense.OVERSIZED_FRAME if oversized else Offense.MALFORMED_FRAME
                )
                self.reputation.penalize(peer_id, offense)
                await write_frame(writer, self._error("bad-frame", str(exc)))
                return
            await self._serve_connection(msg, writer, peer_id)
        finally:
            writer.close()
            await writer.wait_closed()

    async def _serve_connection(
        self,
        msg: dict,
        writer: asyncio.StreamWriter,
        peer_id: str,
    ) -> None:
        """Post-prologue connection body — the subclass-specific success/error tail."""
        raise NotImplementedError

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"kind": "error", "code": code, "message": message}
