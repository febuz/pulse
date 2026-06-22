"""Proofs for #160/#161: the relay carrier honours the #58 piggyback identity proof.

ROOT CAUSE (one fix, two findings). On the relay/mailbox receive path the peer
identity used as the security key was derived from a SELF-ASSERTED, per-frame
re-mintable ``_relay_reply_to`` mailbox, and the receiver DELIBERATELY STRIPPED the
piggybacked identity proof (it lives in the ``_relay_*`` namespace dropped by
``_strip_envelope``). So the carrier id was forever ``relay:<self-asserted mailbox>``
and a sender could rotate it per frame to:

  * (#160) evade a relay ban and reset / LRU-evict its per-peer ServeBudget; and
  * (#161) mint unlimited distinct addrman source groups and spray every new-table
    bucket, defeating the #100 anti-eclipse defence.

THE FIX preserves the proof on the relay receive path and routes it through the SAME
identity gate the TCP path uses (``_resolve_verdict`` → ``PeerIdentityGate.resolve``):
a VALID + FRESH + BOUND + first-seen proof upgrades the carrier id to the proven
``node:<pubkey>`` (so ban gate, ServeBudget, ingest budget AND the PEX source group
all key on the proven identity); an absent / invalid / replayed / mis-bound proof
falls back to ``relay:<mailbox>`` unchanged. No new crypto, no weaker check.

These tests are LOAD-BEARING: reverting the relay proof-passthrough (relay.py
``_dispatch``) makes the rotation/ban/source-group assertions FAIL again. The exact
failing assertions are named in each test's docstring.
"""

import asyncio
import os

from knitweb.core import canonical, crypto
from knitweb.fabric.node import FabricNode
from knitweb.ledger.node import AccountNode
from knitweb.p2p import identity
from knitweb.p2p.addrbook import source_group
from knitweb.p2p.node import AsyncioP2PNode
from knitweb.p2p.relay import (
    ENVELOPE_ID_PROOF_KEY,
    ENVELOPE_PEER_KEY,
    RelayTransport,
    _strip_envelope,
    relay_peer_id,
)
from knitweb.p2p.reputation import Offense
from knitweb.p2p.transport import PeerAddress


def run(coro):
    return asyncio.run(coro)


# --- in-memory relay (mirrors test_relay_auth_gate's fake api/relay) ---------


class InMemoryRelay:
    """A fake ``api/relay`` honouring the send/fetch mailbox contract (no socket).

    Only ever moves opaque base64 frames; it never decodes the canonical payload,
    exactly like the live store-and-forward relay.
    """

    def __init__(self) -> None:
        self.mailboxes: "dict[str, list[dict]]" = {}

    async def post(self, url: str, payload: dict) -> dict:
        if url.endswith("/api/relay/send"):
            mb = payload["mailbox"]
            self.mailboxes.setdefault(mb, []).append(
                {"rid": payload.get("rid"), "frame": payload["frame"]}
            )
            return {"ok": True}
        if url.endswith("/api/relay/fetch"):
            mb = payload["mailbox"]
            queued = self.mailboxes.get(mb, [])
            self.mailboxes[mb] = []
            return {"messages": queued}
        raise AssertionError(f"unexpected relay url {url}")


def relay_for(mailbox: str, relay: InMemoryRelay) -> RelayTransport:
    return RelayTransport(base_url="https://r", mailbox=mailbox, poster=relay)


def _srv_peer() -> PeerAddress:
    return PeerAddress(transport="relay", params={"mailbox": "srv", "base_url": "https://r"})


def _e2e_capture_keys(make_server, make_client, *, body, rotations, proven, on_first=None):
    """Drive REAL RelayTransport dials from a proof-stamping client into a server,
    rotating the client's reply-to mailbox each dial, and capture per-dial the
    server's resolved serve key and threaded source id. This routes through
    relay.py ``_dispatch`` so reverting the proof-passthrough flips the outcome
    (load-bearing). ``on_first(server)`` runs once before dialing (e.g. to ban).
    Returns ``(serve_keys, source_ids, outs)``."""
    serve_keys: list = []
    source_ids: list = []
    outs: list = []

    async def scenario():
        relay = InMemoryRelay()
        server = make_server(relay)
        server._id_proof_now = lambda: _FIXED_NOW
        client = make_client(relay)
        client._id_proof_now = lambda: _FIXED_NOW
        if on_first is not None:
            on_first(server)

        orig_route = server._route

        def route(kind, msg, source_id=None):
            serve_keys.append(getattr(server, "_serve_peer_key", None))
            source_ids.append(source_id)
            return orig_route(kind, msg, source_id)

        server._route = route

        # FabricNode dials via _send, AsyncioP2PNode via _roundtrip; both stamp the
        # proof and route by transport identically.
        dial = getattr(client, "_send", None) or client._roundtrip
        async with server, client:
            for i in range(rotations):
                # Rotate the self-asserted reply-to mailbox per frame (the attack).
                client.transport.mailbox = f"rot-{i}"
                outs.append(await dial(_srv_peer(), dict(body)))

    run(scenario())
    return serve_keys, source_ids, outs


# --- proof helpers: build a relay-delivered request exactly as the carrier does ---

_FIXED_NOW = 1000


def _net(priv: str) -> str:
    return identity.network_signing_key(priv)


def _proven_id(priv: str) -> str:
    return identity.node_peer_id(crypto.public_from_private(_net(priv)))


def _body_binding(body: dict) -> bytes:
    return crypto.sha256(canonical.encode(body))


def _proof_record(priv: str, *, body: dict, ts: int = _FIXED_NOW, nonce=None) -> dict:
    """A transport-envelope id-proof record, network-keyed and bound to ``body``."""
    proof = identity.make_id_proof(
        _net(priv),
        nonce=nonce if nonce is not None else os.urandom(identity.NONCE_LEN),
        timestamp=ts,
        binding=_body_binding(body),
    )
    return identity.id_proof_to_record(proof)


def _relay_delivered(body: dict, mailbox: str, proof_record=None) -> dict:
    """The map the relay carrier hands ``_dispatch`` after the FIX: the business
    body, the stamped ``relay:<mailbox>`` peer key, and (when present) the preserved
    identity proof — i.e. exactly what relay.py ``_dispatch`` now produces."""
    req = dict(body)
    req[ENVELOPE_PEER_KEY] = relay_peer_id(mailbox)
    if proof_record is not None:
        req[ENVELOPE_ID_PROOF_KEY] = proof_record
    return req


# =====================================================================
# #160 — ban gate + ServeBudget key on the PROVEN id, not the mailbox
# =====================================================================


def _serve_keying_node():
    """A FabricNode with a deterministic verifier clock and a hook that captures the
    ``_serve_peer_key`` resolved during each dispatch (the ServeBudget/ingest key)."""
    node = FabricNode()
    node._id_proof_now = lambda: _FIXED_NOW
    captured = {}
    orig_route = node._route

    def route(kind, msg, source_id=None):
        captured["serve_key"] = node._serve_peer_key
        return orig_route(kind, msg, source_id)

    node._route = route
    return node, captured


def _fabric_pair(relay, client_priv):
    """A FabricNode server (keyless mailbox ``srv``) and a FabricNode client that
    stamps proofs from ``client_priv`` (mailbox ``cli``)."""
    server = FabricNode(transport=relay_for("srv", relay))
    client = FabricNode(priv=client_priv, transport=relay_for("cli", relay))
    return server, client


def test_160_serve_budget_keys_on_proven_id_across_mailbox_rotation():
    """LOAD-BEARING (#160 ServeBudget leg) — drives the REAL RelayTransport.

    A relay peer with a VALID proof gets a STABLE proven ``node:<pubkey>`` serve key
    across many frames even as it rotates the reply-to mailbox.

    Revert the relay proof-passthrough (relay.py ``_dispatch``) → the proof is
    stripped → every frame keys on a fresh ``relay:<rot-i>`` and THIS assertion
    FAILS::

        assert set(serve_keys) == {proven}
    """
    client_priv, _ = crypto.generate_keypair()
    proven = _proven_id(client_priv)
    body = {"kind": "fabric-sync-request"}

    serve_keys, _src, _outs = _e2e_capture_keys(
        lambda relay: _fabric_pair(relay, client_priv)[0],
        lambda relay: _fabric_pair(relay, client_priv)[1],
        body=body,
        rotations=8,
        proven=proven,
    )
    # All eight rotated mailboxes resolved to the ONE proven id — rotation cannot
    # reset the byte budget nor LRU-evict an honest peer's bucket.
    assert set(serve_keys) == {proven}


def test_160_ban_is_not_evaded_by_rotating_the_mailbox():
    """LOAD-BEARING (#160 ban leg) — drives the REAL RelayTransport.

    Once the proven id is banned, rotating the mailbox does NOT evade the ban: every
    rotated dial is refused with a ``banned`` error.

    Revert the proof-passthrough → the banned proven id is never resolved → each
    rotated mailbox is an unbanned ``relay:<rot-i>`` and is SERVED, so THIS
    assertion FAILS::

        assert all(o.get("code") == "banned" for o in outs)
    """
    client_priv, _ = crypto.generate_keypair()
    proven = _proven_id(client_priv)
    body = {"kind": "fabric-sync-request"}

    def ban_proven(server):
        server.reputation.penalize(proven, Offense.EQUIVOCATION)  # instant ban
        assert server.reputation.is_banned(proven)

    _serve, _src, outs = _e2e_capture_keys(
        lambda relay: _fabric_pair(relay, client_priv)[0],
        lambda relay: _fabric_pair(relay, client_priv)[1],
        body=body,
        rotations=5,
        proven=proven,
        on_first=ban_proven,
    )
    assert outs and all(o.get("code") == "banned" for o in outs)


# --- fast consumer-level checks (hand-built post-relay request) --------------


def test_160_serve_key_resolution_unit():
    """The serve/ingest key resolution upgrades a relay carrier to the proven id when
    a valid proof is present on the dispatched request (consumer-level unit)."""
    priv, _ = crypto.generate_keypair()
    proven = _proven_id(priv)
    node, captured = _serve_keying_node()
    body = {"kind": "fabric-sync-request"}
    serve_keys = set()
    for i in range(4):
        req = _relay_delivered(body, f"mb-{i}", _proof_record(priv, body=body))
        run(node._dispatch(req))
        serve_keys.add(captured["serve_key"])
    assert serve_keys == {proven}


# =====================================================================
# #161 — addrman source group keys on the PROVEN id, not the mailbox
# =====================================================================


def _pex_source_node():
    """An AsyncioP2PNode (which owns PEX/addrbook) with a deterministic verifier
    clock and a hook capturing the ``source_id`` threaded into ``_route``."""
    acct_priv, acct_pub = crypto.generate_keypair()
    node = AsyncioP2PNode(account=AccountNode(priv=acct_priv, pub=acct_pub))
    node._id_proof_now = lambda: _FIXED_NOW
    captured = {}
    orig_route = node._route

    def route(kind, msg, source_id=None):
        captured["source_id"] = source_id
        return orig_route(kind, msg, source_id)

    node._route = route
    return node, captured


def _asyncio_pair(relay, client_priv, client_pub):
    server = AsyncioP2PNode(transport=relay_for("srv", relay))
    client = AsyncioP2PNode(
        account=AccountNode(priv=client_priv, pub=client_pub),
        transport=relay_for("cli", relay),
    )
    return server, client


def test_161_rotated_mailboxes_collapse_to_one_source_group():
    """LOAD-BEARING (#161 anti-eclipse leg) — drives the REAL RelayTransport.

    N rotated mailboxes from ONE proven identity collapse to ONE addrman source
    group, so the sender cannot spray every new-table bucket.

    Revert the proof-passthrough → each rotated mailbox is a distinct
    ``relay:<rot-i>`` source → N distinct source groups → THIS assertion FAILS::

        assert len(groups) == 1
    """
    priv, pub = crypto.generate_keypair()
    proven = _proven_id(priv)
    body = {"kind": "peer-exchange", "peers": []}

    _serve, source_ids, _outs = _e2e_capture_keys(
        lambda relay: _asyncio_pair(relay, priv, pub)[0],
        lambda relay: _asyncio_pair(relay, priv, pub)[1],
        body=body,
        rotations=8,
        proven=proven,
    )
    groups = {source_group(AsyncioP2PNode._pex_source(s)) for s in source_ids}

    assert len(groups) == 1
    # And it is the stable per-identity group, not a mailbox group.
    (only,) = groups
    assert only.startswith(b"src:name:node:")


def test_161_distinct_identities_keep_distinct_source_groups():
    """Two different proven identities still map to two different source groups —
    the collapse is per-identity, not a global merge that would weaken the defence."""
    priv_a, _ = crypto.generate_keypair()
    priv_b, _ = crypto.generate_keypair()
    node, captured = _pex_source_node()
    body = {"kind": "peer-exchange", "peers": []}

    run(node._dispatch(_relay_delivered(body, "mbA", _proof_record(priv_a, body=body))))
    grp_a = source_group(node._pex_source(captured["source_id"]))
    run(node._dispatch(_relay_delivered(body, "mbB", _proof_record(priv_b, body=body))))
    grp_b = source_group(node._pex_source(captured["source_id"]))

    assert grp_a != grp_b


# =====================================================================
# NEGATIVE / anti-spoof — an unacceptable proof must NOT upgrade the key
# =====================================================================


def test_negative_misbound_proof_falls_back_to_mailbox():
    """An INVALID (mis-bound) proof does NOT upgrade the key — it stays
    ``relay:<mailbox>`` (else we'd reintroduce a spoof worse than the bug)."""
    priv, _ = crypto.generate_keypair()
    node, captured = _serve_keying_node()
    body = {"kind": "fabric-sync-request"}
    # Proof bound to a DIFFERENT body — the verifier recomputes a different binding.
    bad = _proof_record(priv, body={"kind": "something-else"})

    req = _relay_delivered(body, "mb-bad", bad)
    run(node._dispatch(req))
    assert captured["serve_key"] == relay_peer_id("mb-bad")


def test_negative_replayed_proof_falls_back_to_mailbox():
    """A REPLAYED (verbatim) proof is accepted at most once; the second presentation
    falls back to ``relay:<mailbox>`` (anti-replay seen-cache, #90)."""
    priv, _ = crypto.generate_keypair()
    proven = _proven_id(priv)
    node, captured = _serve_keying_node()
    body = {"kind": "fabric-sync-request"}
    rec = _proof_record(priv, body=body)  # one fixed proof, presented twice

    run(node._dispatch(_relay_delivered(body, "rep-1", rec)))
    first = captured["serve_key"]
    run(node._dispatch(_relay_delivered(body, "rep-2", rec)))
    second = captured["serve_key"]

    assert first == proven
    assert second == relay_peer_id("rep-2")


def test_proofless_relay_peer_still_works_on_mailbox_key():
    """A proofless relay peer is unchanged: it keys on ``relay:<mailbox>`` and is
    served — preserving today's behaviour for every pre-#58 relay peer."""
    node, captured = _serve_keying_node()
    body = {"kind": "fabric-sync-request"}

    out = run(node._dispatch(_relay_delivered(body, "no-proof")))
    assert captured["serve_key"] == relay_peer_id("no-proof")
    assert out.get("kind") == "fabric-sync-data"  # served, not gated


# =====================================================================
# End-to-end over the real RelayTransport carrier (proof rides the frame)
# =====================================================================


def test_end_to_end_relay_carrier_upgrades_to_proven_id():
    """A real RelayTransport roundtrip: the dialer stamps a proof, the receiver
    preserves it through ``_dispatch`` and resolves the proven id — proving the
    passthrough wires up over the actual carrier, not just a hand-built request."""

    async def scenario():
        relay = InMemoryRelay()
        priv, pub = crypto.generate_keypair()
        server = AsyncioP2PNode(transport=relay_for("srv", relay))
        server._id_proof_now = lambda: _FIXED_NOW
        client = AsyncioP2PNode(
            account=AccountNode(priv=priv, pub=pub),
            transport=relay_for("cli", relay),
        )
        client._id_proof_now = lambda: _FIXED_NOW

        seen = {}
        orig_route = server._route

        def route(kind, msg, source_id=None):
            seen["source_id"] = source_id
            return orig_route(kind, msg, source_id)

        server._route = route

        async with server, client:
            # A PEX dial carries the client's stamped identity proof over the relay.
            await client._roundtrip(
                PeerAddress(transport="relay", params={"mailbox": "srv", "base_url": "https://r"}),
                {"kind": "peer-exchange", "peers": []},
            )
        return seen, _proven_id(priv)

    seen, proven = run(scenario())
    # The server resolved the carrier to the client's PROVEN id over the live relay.
    assert seen["source_id"] == proven


# =====================================================================
# BYTE-IDENTITY PIN — honouring the proof changes NO canonical/hashed bytes
# =====================================================================


def test_byte_identity_pin_proof_passthrough_changes_no_canonical_bytes():
    """Deterministic record (fixed priv key): the fresh Knit CID, the canonical-CBOR
    SHA-256, and the signable-bytes SHA-256 are IDENTICAL whether or not a proof is
    honoured on the relay path — the proof lives in the ``_relay_*`` transport
    envelope, stripped before any signed/business logic. (We do NOT pin the
    frame/envelope SHA: the ECDSA nonce makes it non-deterministic, #131.)"""
    from knitweb.ledger import knit

    priv = "11" * 32
    pub = crypto.public_from_private(priv)
    _, recv = crypto.generate_keypair()
    a = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=7, from_nonce=3, timestamp=0
    )
    rec = a.to_record()

    cid_before = a.id
    canon_before = crypto.sha256(canonical.encode(rec)).hex()
    signable_before = crypto.sha256(a.signing_bytes).hex()

    # Stamp + carry the business body through the relay envelope the way the carrier
    # does, then strip — the carried business map must be byte-identical.
    node = AsyncioP2PNode(account=AccountNode(priv=priv, pub=pub))
    business = {"kind": "knit-proposal", "knit": rec}
    stamped = node._stamp_id_proof(business)
    assert ENVELOPE_ID_PROOF_KEY in stamped  # a proof DID ride along
    carried = _strip_envelope(stamped)
    assert carried == business

    # Rebuild the identical knit and re-measure — nothing the proof touched moved.
    again = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=7, from_nonce=3, timestamp=0
    )
    cid_after = again.id
    canon_after = crypto.sha256(canonical.encode(again.to_record())).hex()
    signable_after = crypto.sha256(again.signing_bytes).hex()

    assert cid_after == cid_before
    assert canon_after == canon_before
    assert signable_after == signable_before
