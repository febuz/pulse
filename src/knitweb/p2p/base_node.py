"""Shared carrier+reputation+metric scaffolding for the two node stacks (#23).

Both :class:`knitweb.p2p.node.AsyncioP2PNode` and
:class:`knitweb.fabric.node.FabricNode` independently grew the *same*
carrier-agnostic machinery: they construct a listening :class:`Transport` plus a
routing :class:`Dialer`, expose the same ``address``/``host``/``port`` accessors
and ``add_transport``, run the same ``start``/``stop`` lifecycle wiring the
listener to ``self._dispatch``, drive the same opt-in anti-entropy loop, hold a
:class:`PeerReputation` and a :class:`Metrics` bag, and gate every request
through the same banned-peer + malformed/oversized-frame + invalid-signature
logic.

``BaseNode`` extracts exactly that shared part — and nothing that touches a
signed/canonical/hash path. Each subclass keeps its own payload handlers, its
own routing table (``_route``), its caught-exception set (``_dispatch_errors``),
and its banned-branch ``frames_out`` policy (``_count_frames_out_on_banned``).

The whole Byzantine-consequence loop now lives on the single carrier-agnostic
:meth:`_dispatch` seam (#52): every carrier that can identify a sender stamps its
id (the relay from its reply mailbox, TCP from the remote IP), and ``_dispatch``
applies the ban gate + the INVALID_SIGNATURE penalty uniformly — so the live
``start() -> transport.listen(_dispatch)`` socket path enforces reputation, not
only the direct-stream :meth:`_handle_peer` wrapper (which is now a thin adapter
over that same seam, kept for socket-free proofs + the hole-punch seam). This is
behavior-preserving for the wire bytes, the ban thresholds, and the metric names,
so a synced Knit's CID is byte-identical.
"""

from __future__ import annotations

import asyncio
import time

from ..core import canonical, crypto
from . import identity
from .anti_entropy import AntiEntropy, Backoff
from .metrics import Metrics
from .peer_identity_gate import PeerIdentityGate
from .relay import ENVELOPE_ID_PROOF_KEY, ENVELOPE_PEER_KEY
from .reputation import Offense, PeerReputation
from .transport import Dialer, PeerAddress, TcpTransport, Transport, tcp_peer_id
from .wire import WireError, read_frame, write_frame

__all__ = ["BaseNode"]


class BaseNode:
    """The carrier-agnostic half both node stacks share verbatim.

    Owns only the five shared instance fields (``transport``, ``dialer``,
    ``reputation``, ``metrics``, plus the ``_listening`` / ``_anti_entropy_task``
    handles); a subclass calls ``super().__init__(...)`` first and then sets its
    own (account/feeds/web/keypair/…) state. The polymorphic seams a subclass
    must supply are ``_route``, ``_dispatch_errors``,
    ``_count_frames_out_on_banned`` and ``_anti_entropy_rounds``.
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
        # The single identity-keying authority (#65): one gate now owns BOTH the
        # challenge-response and the no-round-trip piggyback resolution of a
        # connection's reputation key (proven ``node:<pubkey>`` when a valid+fresh
        # proof rides the request, else the carrier ``tcp:``/``relay:`` key) and
        # routes the resulting penalty through that same key. It shares this node's
        # ledger, so feed-level offences charged elsewhere stay on the one ledger.
        # The inline piggyback-only resolution that #61 shipped is gone; the live
        # path now flows through the gate, preserving its behaviour exactly while
        # the challenge-response path becomes additionally available.
        self.identity_gate = PeerIdentityGate(
            self.reputation, proof_window_s=self._id_proof_window_s
        )
        # Integer-only observability over the wire path (frames in/out,
        # malformed/oversized frames, banned-peer refusals, …). Node-local
        # bookkeeping only: it touches no signed record and no hash path, so a
        # synced Knit's CID is byte-identical whether or not this node is metered.
        self.metrics = Metrics()
        self._listening = False
        # Opt-in self-healing convergence loop (issue #44). Off by default — the
        # handle lets stop() cancel the loop cleanly.
        self._anti_entropy_task: "asyncio.Task | None" = None
        # Opt-in gossipsub mesh-maintenance loop (issue #78). Off by default and
        # only meaningful on a subclass that exposes a gossip heartbeat tick (the
        # base node has none, so its public start_gossip lives on the subclass);
        # the handle lets stop() cancel the loop cleanly, mirroring anti-entropy.
        self._gossip_task: "asyncio.Task | None" = None

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
        # Hand the carrier both the dispatch seam and the frame-fault callback, so
        # a malformed/oversized frame from an identified peer accrues its graded
        # reputation penalty on the LIVE path (not only on the test-only stream).
        await self.transport.listen(self._dispatch, self._on_frame_fault)
        self._listening = True

    async def stop(self) -> None:
        """Stop the listener (and any running anti-entropy / gossip loop)."""
        await self.stop_anti_entropy()
        await self.stop_gossip()
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

    # -- gossipsub mesh maintenance (issue #78) ---------------------------

    async def stop_gossip(self) -> None:
        """Cancel the background gossip-maintenance loop if one is running.

        The exact mirror of :meth:`stop_anti_entropy`: clear the handle, cancel
        the task, and await its cancellation so the loop tears down cleanly.
        """
        task = self._gossip_task
        self._gossip_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _gossip_sleep(delay: int) -> None:
        # The prod clock: a seconds-based asyncio sleep. Tests inject a virtual
        # clock so the cadence is deterministic with no real time. No wall-clock
        # ever enters the loop's decisions — the only notion of time is the
        # injected integer cadence and the gossip heartbeat's integer epoch.
        await asyncio.sleep(delay)

    async def _gossip_run(self, tick, interval: int, sleep) -> None:
        # Drive the gossip heartbeat forever (until cancelled). A raised tick is
        # swallowed (mirrors AntiEntropy's failed-round swallow) so one bad round
        # — an offline peer this cycle — never crashes the loop; the mesh
        # re-steers on the next heartbeat.
        try:
            while True:
                try:
                    await tick()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                await sleep(interval)
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

        The single seam the listening :class:`Transport` feeds every decoded
        request to (TCP accept loop or relay mailbox poll alike) — and, via
        :meth:`_handle_peer`, the seam any direct-stream caller funnels into too.
        Every carrier that can positively identify the sender stamps that identity
        onto the request as :data:`ENVELOPE_PEER_KEY` (the relay from its reply
        mailbox, the TCP carrier from the remote IP); here we honour the *same* ban
        gate before any work and the *same* INVALID_SIGNATURE penalty on a forged
        author signature, then drop the key so it never reaches signed/business
        logic. This is what makes the reputation/ban layer ACTIVE on the live TCP
        path, not only the test-only direct-stream path (#52).

        Routing, the caught-exception set, and the banned-branch ``frames_out``
        policy are subclass seams (``_route`` / ``_dispatch_errors`` /
        ``_count_frames_out_on_banned``), so each node keeps its exact behavior.

        Proven node identity (step 2 of #58): if the request also carries a valid
        OPTIONAL piggybacked identity proof (:data:`ENVELOPE_ID_PROOF_KEY`), the
        reputation key is upgraded to the proven ``node:<pubkey>`` instead of the
        carrier's ``tcp:<ip>``/``relay:<mailbox>`` id — so the ban gate and every
        reputation penalty land on the *key*, and a forger behind a shared NAT IP
        no longer collateral-bans an honest co-located peer that presents its own
        proof. An absent/invalid/expired proof falls back to the carrier id, so
        every pre-#58 peer and test is byte-for-byte unchanged.
        """
        self.metrics.incr("frames_in")
        carrier_id = msg.pop(ENVELOPE_PEER_KEY, None)
        if not isinstance(carrier_id, str):
            carrier_id = None
        # Resolve the reputation key through the ONE identity-keying authority
        # (#65). Always pop the proof envelope key (even on a bad proof) so it
        # never reaches signed/business logic on the live TCP path — on the relay
        # path it is already stripped, but a TCP frame carries it verbatim here.
        verdict = self._resolve_verdict(
            carrier_id,
            msg.pop(ENVELOPE_ID_PROOF_KEY, None),
            body=msg,
        )
        if verdict is not None and not verdict.accepted:
            self.metrics.incr("banned_refusals")
            if self._count_frames_out_on_banned:
                self.metrics.incr("frames_out")
            return self._error("banned", "peer is banned")
        try:
            out = self._route(msg.get("kind"), msg)
        except self._dispatch_errors as exc:
            # A forged author signature surfaces here as a routing error mentioning
            # "signature"; with a positively-identified sender it is a graded
            # INVALID_SIGNATURE offense, routed through the gate so it lands on the
            # verdict's resolved key (the proven node:<pubkey> when a proof rode
            # along, else the carrier id) — the one keying path for every carrier.
            if verdict is not None and "signature" in str(exc):
                self.identity_gate.penalize(verdict, Offense.INVALID_SIGNATURE)
            out = self._error("bad-request", str(exc))
        self.metrics.incr("frames_out")
        return out

    # -- proven node identity (step 2 of #58) -----------------------------

    #: Freshness window (integer seconds) a piggybacked proof's timestamp must lie
    #: within. A pure integer policy knob; touches no canonical/hashed bytes.
    _id_proof_window_s: int = identity.DEFAULT_PROOF_WINDOW_S

    def _id_proof_now(self) -> int:
        """The verifier's coarse integer clock (seconds). Overridable in tests.

        Default is the wall clock truncated to whole seconds. A test injects a
        fixed integer so a piggybacked proof's freshness check is deterministic.
        """
        return int(time.time())

    @staticmethod
    def _id_proof_binding(body) -> bytes:
        """The connection/body context a piggyback proof is bound to (#90).

        A 32-byte SHA-256 over the canonical-CBOR of the business request ``body``
        (the request map *after* the carrier's ``_relay_*`` envelope keys have been
        popped/stripped — exactly the bytes both peers agree on across any carrier
        hop). Binding to the body means a proof captured on one exchange cannot be
        lifted onto a different first-message body: the verifier recomputes a
        different digest, the binding no longer matches, and the proof is rejected
        to carrier fallback. Deterministic and integer-/byte-only; touches no
        signed/canonical *record* bytes (it only HASHES the already-canonical
        request envelope, never re-signs or mutates it), so no Knit's CID changes.
        """
        if not isinstance(body, dict):
            return b""
        return crypto.sha256(canonical.encode(body))

    def _resolve_verdict(self, carrier_id, raw_proof, *, body=None):
        """Resolve the connection's :class:`GateVerdict` via the identity gate.

        ``carrier_id`` is the ``tcp:<ip>``/``relay:<mailbox>`` id the carrier
        stamped (or ``None`` when the carrier could not identify the sender);
        ``raw_proof`` is the popped, OPTIONAL :data:`ENVELOPE_ID_PROOF_KEY` value;
        ``body`` is the business request map the proof is bound to (#90).

        When ``carrier_id`` is ``None`` there is no key to judge and the carrier
        contributed no identity, so this returns ``None`` and ``_dispatch`` skips
        both the ban gate and the penalty (exactly as the pre-#65 inline path did
        for an unidentified sender). Otherwise the gate resolves the reputation
        key: a piggybacked :class:`~knitweb.p2p.identity.PiggybackProof` that
        verifies, is fresh within ``_id_proof_window_s`` of :meth:`_id_proof_now`,
        carries the expected connection/body binding, AND has not already been seen
        within the window upgrades the key to the proven ``node:<pubkey>``; an
        absent/tampered/expired/replayed/mis-bound proof falls back to ``carrier_id``
        unchanged (so the existing IP-/mailbox-keyed behaviour is preserved).
        """
        if carrier_id is None:
            return None
        proof = (
            identity.id_proof_from_record(raw_proof)
            if raw_proof is not None
            else None
        )
        return self.identity_gate.resolve(
            carrier_id,
            proof=proof,
            now=self._id_proof_now(),
            binding=self._id_proof_binding(body),
        )

    def _resolve_peer_id(self, carrier_id, raw_proof) -> "str | None":
        """Back-compat accessor: the resolved reputation key (or ``None``).

        Retained as the pre-#65 public seam — it now delegates to the single
        identity gate via :meth:`_resolve_verdict` rather than re-implementing the
        proof check inline, so there is exactly ONE identity-keying path. Returns
        the proven ``node:<pubkey>`` when a valid+fresh proof rode along, else the
        carrier id unchanged.
        """
        verdict = self._resolve_verdict(carrier_id, raw_proof)
        return verdict.rep_key if verdict is not None else carrier_id

    def _id_signing_key(self) -> "str | None":
        """The node's FINANCIAL/Knit-signing secp256k1 private key (hex), or None.

        Subclass seam: a node that owns a stable account/feed key returns it so its
        dials carry a piggybacked identity proof; a keyless node returns ``None``
        and simply dials without a proof (falling back to the carrier id on the
        receiver). Default is keyless.

        SECURITY (#89): this is the *financial* key — it is NOT what signs or ships
        in the network proof. :meth:`_id_network_signing_key` derives a separate,
        unlinkable NETWORK key from it; that network key is the only key material
        that ever reaches a dispatched envelope. The financial public key MUST NEVER
        appear in any dispatched network envelope.
        """
        return None

    def _id_network_signing_key(self) -> "str | None":
        """The node's NETWORK identity private key (hex) for outbound proofs, or None.

        Fix for #89 (deanonymization). Derived one-way from the financial key via
        :func:`knitweb.p2p.identity.network_signing_key`, so the public key that
        rides in every dispatched envelope is cryptographically UNLINKABLE to the
        node's financial/Knit-signing public key (the wallet) — a passive observer
        beside the TCP source IP can no longer tie ``IP -> pubkey -> Knit-signer``.
        The derivation is deterministic, so the network identity is STABLE per node
        across reconnects/IP rotations, which keeps the NAT collateral-ban fix (#58)
        intact. Returns ``None`` for a keyless node (no proof, carrier fallback).
        """
        financial = self._id_signing_key()
        if financial is None:
            return None
        return identity.network_signing_key(financial)

    def _stamp_id_proof(self, request: dict) -> dict:
        """Attach an OPTIONAL fresh identity proof to an outbound ``request``.

        Returns ``request`` unchanged when this node is keyless. Otherwise returns
        a shallow copy with :data:`ENVELOPE_ID_PROOF_KEY` set to a freshly minted
        proof (new random nonce + this node's current coarse timestamp + a binding
        over the request body), signed with this node's NETWORK identity key (#89),
        so the receiver can key reputation on this node's proven, unlinkable
        ``node:<network-pubkey>``. The proof is bound (#90) to the canonical-CBOR of
        the business ``request`` so a captured proof cannot be lifted onto a
        different body. The proof rides only in the stripped ``_relay_*`` envelope
        namespace — it never enters the canonical/hashed bytes the carrier frames,
        so a Knit's CID is unchanged whether or not a proof is attached, and the
        financial public key never appears anywhere in the dispatched envelope.
        """
        signing_key = self._id_network_signing_key()
        if signing_key is None:
            return request
        binding = self._id_proof_binding(request)
        proof = identity.make_id_proof(
            signing_key, timestamp=self._id_proof_now(), binding=binding
        )
        stamped = dict(request)
        stamped[ENVELOPE_ID_PROOF_KEY] = identity.id_proof_to_record(proof)
        return stamped

    def _on_frame_fault(self, peer_id: str, exc: WireError) -> dict:
        """Carrier callback: a malformed/oversized frame from an identified peer.

        The carrier owns the *framing* but not *reputation*, so when it cannot even
        decode a frame from a positively-identified sender it calls back here: the
        node records the graded penalty + the matching frame-fault counter and
        returns the error map to write back. Shared by the live TCP carrier (via
        ``transport.listen``) and the direct-stream :meth:`_handle_peer` wrapper, so
        the two never drift.
        """
        oversized = "too large" in str(exc)
        self.metrics.incr("frames_oversized" if oversized else "frames_malformed")
        offense = Offense.OVERSIZED_FRAME if oversized else Offense.MALFORMED_FRAME
        self.reputation.penalize(peer_id, offense)
        return self._error("bad-frame", str(exc))

    def _route(self, kind, msg: dict) -> dict:
        """Subclass routing table: kind -> handler. Raises an unknown-kind error."""
        raise NotImplementedError

    # -- direct-stream wrapper (test + future hole-punch seam) ------------

    @staticmethod
    def _peer_id(writer: asyncio.StreamWriter) -> str | None:
        """The ``tcp:<ip>`` reputation key for a connected peer (or None).

        Matches what the live :class:`~knitweb.p2p.transport.TcpTransport` accept
        loop stamps, so a peer's ban verdict is identical whether a request arrives
        over the real accept loop or this direct-stream wrapper. Keyed on the
        remote IP only — the port is ephemeral, so ``host:port`` would mint a fresh
        identity per reconnect and a repeat forger could never be banned.
        """
        peername = writer.get_extra_info("peername")
        if isinstance(peername, tuple) and len(peername) >= 1 and peername[0]:
            return tcp_peer_id(str(peername[0]))
        return None

    async def _handle_peer(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Direct-stream wrapper that funnels a single TCP frame into ``_dispatch``.

        This is now a thin adapter over the same shared seam the live accept loop
        uses: it derives the peer id the carrier would stamp, applies the
        malformed/oversized frame fault via :meth:`_on_frame_fault`, then re-stamps
        the id and hands the decoded request to :meth:`_dispatch` — so the ban
        gate, the signature penalty, and routing are byte-for-byte the live path's,
        with no per-subclass ``_serve_connection`` duplication to drift (#52 dedup).
        Retained for the deterministic socket-free property proofs and as the
        hole-punch seam (a future transport can reuse it verbatim).
        """
        peer_id = self._peer_id(writer)
        try:
            try:
                msg = await read_frame(reader)
            except WireError as exc:
                if peer_id is not None:
                    await write_frame(writer, self._on_frame_fault(peer_id, exc))
                else:
                    await write_frame(writer, self._error("bad-frame", str(exc)))
                return
            if peer_id is not None:
                msg[ENVELOPE_PEER_KEY] = peer_id
            out = await self._dispatch(msg)
            await write_frame(writer, out)
        finally:
            writer.close()
            await writer.wait_closed()

    @staticmethod
    def _error(code: str, message: str) -> dict:
        return {"kind": "error", "code": code, "message": message}
