"""Proofs for the #38 fix: the reputation ban gate now covers the relay carrier.

A banned peer used to exchange freely over ``relay://`` because the ban gate lived
only in the TCP ``_handle_peer`` wrapper, never in the carrier-agnostic
``_dispatch`` the relay poll loop funnels into. The fix stamps the relay sender's
identity onto the request as a *transport-envelope* key
(:data:`~knitweb.p2p.relay.ENVELOPE_PEER_KEY`) and has both node ``_dispatch``
methods honour the same :meth:`PeerReputation.is_banned` gate before any work.

These tests pin three invariants:

  * the gate is enforced on the relay path for both node stacks (a banned mailbox
    is refused with a ``banned`` error, an unbanned one is served);
  * the envelope peer key is a ``_relay_*`` correlation key, so ``_strip_envelope``
    drops it and it can never reach signed/business logic; and
  * byte-identity is preserved — the carried business payload a handler routes is
    byte-identical with or without the relay hop, and a freshly woven record's CID
    is unchanged, so the gate touches no canonical/hashed bytes.
"""

import asyncio

import pytest

from knitweb.core import canonical
from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode
from knitweb.fabric.web import Web
from knitweb.p2p.node import AsyncioP2PNode
from knitweb.p2p.relay import (
    ENVELOPE_PEER_KEY,
    RelayTransport,
    _strip_envelope,
    relay_peer_id,
)
from knitweb.p2p.reputation import DEFAULT_BAN_THRESHOLD, Offense
from knitweb.p2p.transport import PeerAddress


def run(coro):
    return asyncio.run(coro)


class InMemoryRelay:
    """A fake ``api/relay`` honouring the send/fetch mailbox contract (no socket).

    ``send`` appends a base64 frame to a mailbox queue; ``fetch`` drains and
    returns a mailbox's queued frames. Mirrors the live PHP relay: it only ever
    moves opaque base64 frames and never decodes the canonical-CBOR payload.
    """

    def __init__(self) -> None:
        self.mailboxes: dict[str, list[dict]] = {}

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
    return RelayTransport(base_url="https://5mart.ml", mailbox=mailbox, poster=relay)


def peer_at(mailbox: str) -> PeerAddress:
    return PeerAddress(
        transport="relay",
        params={"mailbox": mailbox, "base_url": "https://5mart.ml"},
    )


# ── 1. relay_peer_id: deterministic, namespaced, distinct from a TCP key ─────


def test_relay_peer_id_is_deterministic_and_namespaced():
    assert relay_peer_id("alice") == relay_peer_id("alice")
    assert relay_peer_id("alice") != relay_peer_id("bob")
    # A relay mailbox key can never collide with a TCP "host:port" key.
    assert relay_peer_id("127.0.0.1:9000") != "127.0.0.1:9000"
    assert relay_peer_id("alice").startswith("relay:")


# ── 2. The envelope peer key is transport-only and stripped before logic ─────


def test_envelope_peer_key_is_a_relay_correlation_key():
    # It must share the _relay_ prefix so _strip_envelope removes it.
    assert ENVELOPE_PEER_KEY.startswith("_relay_")


def test_strip_envelope_drops_the_peer_key():
    carried = {"kind": "feed-request", "feed": "f", "start": 0, "count": None}
    stamped = dict(carried)
    stamped[ENVELOPE_PEER_KEY] = relay_peer_id("srv")
    stamped["_relay_rid"] = 7
    stamped["_relay_reply_to"] = "cli"
    # Every _relay_* key — including the peer id — is dropped, leaving the
    # business map byte-identical to what an author would have framed.
    assert _strip_envelope(stamped) == carried
    assert canonical.encode(_strip_envelope(stamped)) == canonical.encode(carried)


# ── 3. The ban gate is enforced on the relay path (AsyncioP2PNode) ───────────


def test_banned_relay_peer_is_refused_by_asyncio_node_dispatch():
    node = AsyncioP2PNode(transport=relay_for("srv", InMemoryRelay()))
    banned = relay_peer_id("evil")
    node.reputation.penalize(banned, Offense.EQUIVOCATION)  # instant ban
    assert node.reputation.is_banned(banned)

    # A request the relay carrier would hand the handler: business payload plus
    # the stamped transport-envelope peer id.
    req = {"kind": "peer-exchange", "peers": [], ENVELOPE_PEER_KEY: banned}
    out = run(node._dispatch(req))
    assert out == {"kind": "error", "code": "banned", "message": "peer is banned"}


def test_unbanned_relay_peer_is_served_by_asyncio_node_dispatch():
    node = AsyncioP2PNode(transport=relay_for("srv", InMemoryRelay()))
    req = {"kind": "peer-exchange", "peers": [], ENVELOPE_PEER_KEY: relay_peer_id("ok")}
    out = run(node._dispatch(req))
    assert out.get("kind") == "peer-exchange"  # served, not banned


def test_unidentified_relay_request_is_not_gated():
    # No envelope peer id (e.g. a frame with no reply_to) is served normally —
    # the gate only fires on a positively-identified, banned sender.
    node = AsyncioP2PNode(transport=relay_for("srv", InMemoryRelay()))
    out = run(node._dispatch({"kind": "peer-exchange", "peers": []}))
    assert out.get("kind") == "peer-exchange"


# ── 4. The ban gate is enforced on the relay path (FabricNode) ───────────────


def test_banned_relay_peer_is_refused_by_fabric_node_dispatch():
    node = FabricNode(transport=relay_for("srv", InMemoryRelay()))
    banned = relay_peer_id("evil")
    node.reputation.penalize(banned, Offense.INVALID_SIGNATURE)
    node.reputation.penalize(banned, Offense.STALE_OR_FORGED_PROOF)
    assert node.reputation.is_banned(banned)

    before = node.metrics.get("banned_refusals")
    req = {"kind": "fabric-sync-request", ENVELOPE_PEER_KEY: banned}
    out = run(node._dispatch(req))
    assert out == {"kind": "error", "code": "banned", "message": "peer is banned"}
    # The refusal is metered, mirroring the TCP _handle_peer path.
    assert node.metrics.get("banned_refusals") == before + 1


def test_unbanned_relay_peer_is_served_by_fabric_node_dispatch():
    node = FabricNode(transport=relay_for("srv", InMemoryRelay()))
    req = {"kind": "fabric-sync-request", ENVELOPE_PEER_KEY: relay_peer_id("ok")}
    out = run(node._dispatch(req))
    assert out.get("kind") == "fabric-sync-data"


# ── 5. End-to-end over the in-memory relay: a banned mailbox cannot weave ─────


def test_banned_mailbox_cannot_weave_over_the_relay_carrier():
    async def scenario():
        from knitweb.core import crypto
        from knitweb.p2p import identity

        relay = InMemoryRelay()
        server = FabricNode(transport=relay_for("server-mb", relay))
        client = FabricNode(transport=relay_for("client-mb", relay))
        # The client dials with a #58 identity proof, so the relay carrier keys it on
        # its PROVEN node id (not the forgeable reply mailbox) — see #160. The server
        # bans that proven id up front; a mailbox-only ban would now be evaded by
        # mailbox rotation, which is exactly the bug #160 closes.
        proven = identity.node_peer_id(
            crypto.public_from_private(identity.network_signing_key(client._priv))
        )
        server.reputation.penalize(proven, Offense.EQUIVOCATION)
        async with server, client:
            client.add_peer("server", peer_at("server-mb"))
            await client.weave(
                {"kind": "knowledge", "title": "a", "body": "x", "author": client.pub}
            )
            # Let the server poll + dispatch a few ticks.
            for _ in range(20):
                if server.web.size != (0, 0):
                    break
                await asyncio.sleep(0.02)
        return server

    server = run(scenario())
    # The banned peer's record never wove into the server's Web.
    assert server.web.size == (0, 0)


def test_unbanned_mailbox_converges_over_the_relay_carrier():
    async def scenario():
        relay = InMemoryRelay()
        server = FabricNode(transport=relay_for("server-mb", relay))
        client = FabricNode(transport=relay_for("client-mb", relay))
        async with server, client:
            client.add_peer("server", peer_at("server-mb"))
            await client.weave(
                {"kind": "knowledge", "title": "a", "body": "x", "author": client.pub}
            )
            for _ in range(20):
                if server.web.size == (1, 0):
                    break
                await asyncio.sleep(0.02)
        return server, client

    server, client = run(scenario())
    assert server.web.size == client.web.size == (1, 0)
    assert server.state_root == client.state_root


# ── 6. Byte-identity: the gate touches no canonical/hashed bytes ─────────────


def test_relay_gate_leaves_a_woven_records_cid_unchanged():
    """A record woven through a relay-fronted, gated node keeps its bare CID."""
    rec = {"kind": "knowledge", "title": "alpha", "body": "x", "author": "z"}
    bare = Web()
    bare_cid = bare.weave(rec)

    async def scenario():
        node = FabricNode(transport=relay_for("mb", InMemoryRelay()))
        cid = await node.weave(rec)  # no peers → broadcast no-op
        return cid, node

    cid, node = run(scenario())
    assert cid == bare_cid
    assert node.state_root == web_state_root(bare)


def test_gated_dispatch_routes_a_byte_identical_business_payload():
    """The payload the handler routes is byte-identical with or without the hop.

    Stamping ``ENVELOPE_PEER_KEY`` and popping it in ``_dispatch`` must leave the
    served record's CID identical to one woven directly — proving the envelope key
    never enters canonical bytes.
    """
    rec = {"kind": "knowledge", "title": "beta", "body": "y", "author": "z"}

    async def scenario():
        relay = InMemoryRelay()
        server = FabricNode(transport=relay_for("srv", relay))
        client = FabricNode(transport=relay_for("cli", relay))
        async with server, client:
            client.add_peer("server", peer_at("srv"))
            await client.weave(rec)
            for _ in range(20):
                if server.web.size == (1, 0):
                    break
                await asyncio.sleep(0.02)
        return server

    server = run(scenario())
    bare = Web()
    bare_cid = bare.weave(rec)
    # The server wove the gossiped record under the same CID a bare weave yields.
    assert bare_cid in server.web.nodes
    assert server.web.size == (1, 0)
