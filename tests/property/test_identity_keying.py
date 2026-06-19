"""Property proofs for piggybacked node-identity keying (step 2 of #58).

Pure and deterministic: no sockets, no separate handshake, no extra await — the
proof is *self-minted* and rides on a request the dialer was already sending, so
nothing here can block or deadlock. The nonce and the coarse timestamp are
injected, so every freshness/replay assertion is reproducible.

Two layers are pinned:

  * the primitive (:func:`make_id_proof` / :func:`verify_id_proof` plus the
    record codec) — a valid proof resolves to ``node:<pubkey>``, a tampered or
    stale/future-dated one resolves to ``None``, and the signed message is
    domain-separated from both the challenge proof and a Knit signature; and
  * the carrier-agnostic ``_dispatch`` keying seam — a valid envelope proof
    upgrades the reputation key to ``node:<pubkey>``, while an absent/invalid/
    expired proof falls back to the carrier ``tcp:<ip>``/``relay:<mailbox>`` id
    (so every pre-#58 peer and test is unchanged), and the proof envelope key is
    a stripped ``_relay_*`` key that never enters canonical/hashed bytes.
"""

import asyncio

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.node import FabricNode
from knitweb.ledger import knit
from knitweb.ledger.node import AccountNode
from knitweb.p2p import identity
from knitweb.p2p.node import AsyncioP2PNode
from knitweb.p2p.relay import (
    ENVELOPE_ID_PROOF_KEY,
    ENVELOPE_PEER_KEY,
    _strip_envelope,
    relay_peer_id,
)
from knitweb.p2p.reputation import Offense
from knitweb.p2p.transport import tcp_peer_id

_NONCE = b"\x11" * identity.NONCE_LEN


def run(coro):
    return asyncio.run(coro)


# ── 1. node_peer_id: deterministic, namespaced, distinct from carrier keys ───


@pytest.mark.property
def test_node_peer_id_is_namespaced_and_distinct_from_carrier_keys():
    _, pub = crypto.generate_keypair()
    assert identity.node_peer_id(pub) == identity.node_peer_id(pub)
    assert identity.node_peer_id(pub).startswith("node:")
    # A proven-key id can never collide with a TCP or relay carrier key.
    assert identity.node_peer_id(pub) != tcp_peer_id(pub)
    assert identity.node_peer_id("alice") != relay_peer_id("alice")


# ── 2. The piggyback primitive: sign → verify → node key ─────────────────────


@pytest.mark.property
def test_round_trip_resolves_to_node_key():
    priv, pub = crypto.generate_keypair()
    proof = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    assert proof.pubkey == pub
    # Within the window the proof resolves to this node's key.
    assert identity.verify_id_proof(proof, now=1000) == identity.node_peer_id(pub)
    assert identity.verify_id_proof(proof, now=1000 + identity.DEFAULT_PROOF_WINDOW_S) \
        == identity.node_peer_id(pub)


@pytest.mark.property
def test_tampered_signature_resolves_to_none():
    priv, pub = crypto.generate_keypair()
    good = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    flipped = "00" if good.sig[:2] != "00" else "01"
    tampered = identity.PiggybackProof(
        pubkey=pub, nonce=_NONCE, timestamp=1000, sig=flipped + good.sig[2:]
    )
    assert identity.verify_id_proof(tampered, now=1000) is None
    # Garbage signature hex fails to None, never raises.
    junk = identity.PiggybackProof(pubkey=pub, nonce=_NONCE, timestamp=1000, sig="zz")
    assert identity.verify_id_proof(junk, now=1000) is None


@pytest.mark.property
def test_pubkey_not_equal_signer_resolves_to_none():
    priv, _ = crypto.generate_keypair()
    _, other_pub = crypto.generate_keypair()
    good = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    mismatched = identity.PiggybackProof(
        pubkey=other_pub, nonce=_NONCE, timestamp=1000, sig=good.sig
    )
    assert identity.verify_id_proof(mismatched, now=1000) is None


@pytest.mark.property
def test_expired_and_future_dated_proofs_resolve_to_none():
    priv, _ = crypto.generate_keypair()
    proof = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    window = identity.DEFAULT_PROOF_WINDOW_S
    # Just outside the window in both directions → rejected (stale / clock-ahead).
    assert identity.verify_id_proof(proof, now=1000 + window + 1) is None
    assert identity.verify_id_proof(proof, now=1000 - window - 1) is None


@pytest.mark.property
def test_timestamp_is_inside_the_signed_bytes():
    """A proof cannot be re-stamped with a different claimed time.

    The timestamp is signed, so swapping it (to pass a freshness check) breaks the
    signature and the proof resolves to None.
    """
    priv, pub = crypto.generate_keypair()
    proof = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    restamped = identity.PiggybackProof(
        pubkey=pub, nonce=_NONCE, timestamp=5000, sig=proof.sig  # sig is for ts=1000
    )
    assert identity.verify_id_proof(restamped, now=5000) is None


@pytest.mark.property
def test_injected_nonce_is_deterministic():
    priv, _ = crypto.generate_keypair()
    a = identity.make_id_proof(priv, nonce=_NONCE, timestamp=7)
    b = identity.make_id_proof(priv, nonce=_NONCE, timestamp=7)
    # ECDSA signs with a random k, so the sig bytes differ — but the SIGNED
    # message (the freshness-bearing part injected in tests) is identical, so
    # both proofs verify against the same clock. That is the determinism the
    # tests rely on; the signature itself is intentionally not reproducible.
    assert a.nonce == b.nonce == _NONCE
    assert a.timestamp == b.timestamp == 7
    assert a.message() == b.message() == identity.PIGGYBACK_TAG + (7).to_bytes(8, "big") + _NONCE
    assert identity.verify_id_proof(a, now=7) == identity.verify_id_proof(b, now=7)
    # A default-nonce proof is fresh each call (os.urandom).
    c = identity.make_id_proof(priv, timestamp=7)
    d = identity.make_id_proof(priv, timestamp=7)
    assert c.nonce != d.nonce


@pytest.mark.property
def test_make_id_proof_rejects_wrong_length_nonce_and_bad_timestamp():
    priv, _ = crypto.generate_keypair()
    with pytest.raises(ValueError):
        identity.make_id_proof(priv, nonce=b"short", timestamp=1)
    with pytest.raises(TypeError):
        identity.make_id_proof(priv, nonce=_NONCE, timestamp="1")  # type: ignore[arg-type]


# ── 3. Record codec: integers/bytes/str only, round-trips, rejects junk ──────


@pytest.mark.property
def test_record_round_trip_preserves_the_proof():
    priv, _ = crypto.generate_keypair()
    proof = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    record = identity.id_proof_to_record(proof)
    assert set(record) == {"pubkey", "nonce", "ts", "sig"}
    # The record is CBOR-encodable (integers/bytes/str only) and round-trips.
    assert canonical.decode(canonical.encode(record)) == record
    assert identity.id_proof_from_record(record) == proof


@pytest.mark.property
def test_malformed_proof_record_decodes_to_none():
    assert identity.id_proof_from_record(None) is None
    assert identity.id_proof_from_record("nope") is None
    assert identity.id_proof_from_record({}) is None
    # Wrong field types each fall to None, never raise.
    assert identity.id_proof_from_record(
        {"pubkey": 1, "nonce": _NONCE, "ts": 1, "sig": "ab"}
    ) is None
    assert identity.id_proof_from_record(
        {"pubkey": "ab", "nonce": "not-bytes", "ts": 1, "sig": "ab"}
    ) is None
    assert identity.id_proof_from_record(
        {"pubkey": "ab", "nonce": _NONCE, "ts": True, "sig": "ab"}
    ) is None


# ── 4. Domain separation: a piggyback proof is its own message space ─────────


@pytest.mark.property
def test_piggyback_proof_is_domain_separated():
    """A piggyback proof cannot be lifted into a challenge proof or a Knit sig."""
    priv, pub = crypto.generate_keypair()
    _, recv = crypto.generate_keypair()

    pb = identity.make_id_proof(priv, nonce=_NONCE, timestamp=1000)
    # Distinct tag from the challenge proof → cannot satisfy verify_proof.
    challenge = identity.issue_challenge(nonce=_NONCE)
    assert identity.verify_proof(
        challenge, identity.Proof(pubkey=pub, sig=pb.sig)
    ) is None
    # And it is not a valid Knit signature (canonical record bytes are disjoint).
    a_knit = knit.build(
        from_pub=pub, to_pub=recv, symbol="PLS", amount=1, from_nonce=1, timestamp=0
    )
    assert not crypto.verify(pub, a_knit.signing_bytes, pb.sig)
    # The signed message is the ASCII piggyback tag, never a CBOR map header.
    assert pb.message().startswith(b"knitweb-p2p-identity-piggyback:")
    assert not a_knit.signing_bytes.startswith(identity.PIGGYBACK_TAG[:1])


# ── 5. The _dispatch keying seam upgrades to the proven node key ─────────────


def _proof_envelope(priv: str, *, timestamp: int) -> dict:
    return identity.id_proof_to_record(
        identity.make_id_proof(priv, nonce=_NONCE, timestamp=timestamp)
    )


@pytest.mark.property
def test_valid_proof_keys_ban_on_node_key_not_ip():
    """A forger that presents a proof is gated on its node key, not its IP."""
    priv, pub = crypto.generate_keypair()
    node = FabricNode()
    node._id_proof_now = lambda: 1000  # deterministic verifier clock
    node.reputation.penalize(identity.node_peer_id(pub), Offense.EQUIVOCATION)
    assert node.reputation.is_banned(identity.node_peer_id(pub))

    # The request carries the carrier id (shared NAT IP) AND the forger's proof.
    req = {
        "kind": "fabric-sync-request",
        ENVELOPE_PEER_KEY: tcp_peer_id("203.0.113.7"),
        ENVELOPE_ID_PROOF_KEY: _proof_envelope(priv, timestamp=1000),
    }
    out = run(node._dispatch(req))
    # Gated on node:<pubkey>, not on the IP (the IP itself is NOT banned).
    assert out == {"kind": "error", "code": "banned", "message": "peer is banned"}
    assert not node.reputation.is_banned(tcp_peer_id("203.0.113.7"))


@pytest.mark.property
def test_honest_peer_sharing_a_banned_ip_with_its_own_proof_is_served():
    """NAT collateral gone: an honest peer on a banned IP, with ITS OWN proof,
    is keyed on its own node key and served — not collateral-banned."""
    forger_priv, forger_pub = crypto.generate_keypair()
    honest_priv, honest_pub = crypto.generate_keypair()
    shared_ip = tcp_peer_id("198.51.100.5")

    node = FabricNode()
    node._id_proof_now = lambda: 1000
    # Both the forger's NODE key and the shared IP are banned at this node.
    node.reputation.penalize(identity.node_peer_id(forger_pub), Offense.EQUIVOCATION)
    node.reputation.penalize(shared_ip, Offense.EQUIVOCATION)
    assert node.reputation.is_banned(identity.node_peer_id(forger_pub))
    assert node.reputation.is_banned(shared_ip)

    # The honest peer dials from the SAME (banned) IP but presents its own proof.
    req = {
        "kind": "fabric-sync-request",
        ENVELOPE_PEER_KEY: shared_ip,
        ENVELOPE_ID_PROOF_KEY: _proof_envelope(honest_priv, timestamp=1000),
    }
    out = run(node._dispatch(req))
    # Keyed on node:<honest_pub> (NOT banned) → served, despite the banned IP.
    assert out.get("kind") == "fabric-sync-data"
    assert not node.reputation.is_banned(identity.node_peer_id(honest_pub))


@pytest.mark.property
def test_no_proof_falls_back_to_ip_keying():
    """Backward-compat: a peer with no proof is gated on its carrier IP key."""
    node = FabricNode()
    ip_key = tcp_peer_id("192.0.2.9")
    node.reputation.penalize(ip_key, Offense.EQUIVOCATION)
    req = {"kind": "fabric-sync-request", ENVELOPE_PEER_KEY: ip_key}
    out = run(node._dispatch(req))
    assert out.get("code") == "banned"
    # And an unbanned no-proof peer is served exactly as before.
    out2 = run(node._dispatch(
        {"kind": "fabric-sync-request", ENVELOPE_PEER_KEY: tcp_peer_id("192.0.2.10")}
    ))
    assert out2.get("kind") == "fabric-sync-data"


@pytest.mark.property
def test_expired_or_tampered_proof_falls_back_to_ip_not_accepted():
    """An expired/tampered proof is NOT accepted: keying falls back to the IP."""
    priv, pub = crypto.generate_keypair()
    node = FabricNode()
    node._id_proof_now = lambda: 100000  # far ahead → the proof below is stale
    # The proven node key is banned; the carrier IP is NOT.
    node.reputation.penalize(identity.node_peer_id(pub), Offense.EQUIVOCATION)
    fresh_ip = tcp_peer_id("203.0.113.99")

    # Expired proof (timestamp far in the past) → must NOT key on node:<pub>.
    req = {
        "kind": "fabric-sync-request",
        ENVELOPE_PEER_KEY: fresh_ip,
        ENVELOPE_ID_PROOF_KEY: _proof_envelope(priv, timestamp=1000),
    }
    out = run(node._dispatch(req))
    # Fell back to the (unbanned) IP key → served, NOT gated on the banned node key.
    assert out.get("kind") == "fabric-sync-data"

    # A tampered proof likewise falls back to the IP.
    bad_env = _proof_envelope(priv, timestamp=100000)
    bad_env["sig"] = "00" + bad_env["sig"][2:]  # break the signature
    node2 = FabricNode()
    node2._id_proof_now = lambda: 100000
    node2.reputation.penalize(identity.node_peer_id(pub), Offense.EQUIVOCATION)
    req2 = {
        "kind": "fabric-sync-request",
        ENVELOPE_PEER_KEY: fresh_ip,
        ENVELOPE_ID_PROOF_KEY: bad_env,
    }
    out2 = run(node2._dispatch(req2))
    assert out2.get("kind") == "fabric-sync-data"  # fell back to the IP, served


# ── 6. Byte-identity: the proof key is stripped, never enters canonical bytes ─


@pytest.mark.property
def test_id_proof_envelope_key_is_a_stripped_relay_key():
    # Shares the _relay_ prefix, so the relay carrier's _strip_envelope drops it.
    assert ENVELOPE_ID_PROOF_KEY.startswith("_relay_")
    priv, _ = crypto.generate_keypair()
    carried = {"kind": "fabric-sync-request"}
    stamped = dict(carried)
    stamped[ENVELOPE_ID_PROOF_KEY] = _proof_envelope(priv, timestamp=1)
    stamped["_relay_rid"] = 3
    # Stripping every _relay_* key leaves the business map byte-identical.
    assert _strip_envelope(stamped) == carried
    assert canonical.encode(_strip_envelope(stamped)) == canonical.encode(carried)


@pytest.mark.property
def test_stamping_a_proof_leaves_a_fresh_knit_cid_unchanged():
    """Byte-identity: carrying a proof in the request envelope cannot change a
    signed record's CID — the proof never enters canonical/hashed bytes."""
    sender_priv, sender_pub = crypto.generate_keypair()
    _, recv = crypto.generate_keypair()
    a_knit = knit.build(
        from_pub=sender_pub, to_pub=recv, symbol="PLS", amount=42,
        from_nonce=7, timestamp=0,
    )
    cid_before = a_knit.id

    # A node frames a knit-proposal AND stamps its identity proof onto it.
    node = AsyncioP2PNode(account=AccountNode(priv=sender_priv, pub=sender_pub))
    request = {"kind": "knit-proposal", "knit": a_knit.to_record()}
    stamped = node._stamp_id_proof(request)
    assert ENVELOPE_ID_PROOF_KEY in stamped
    # The carried knit record (after the envelope key is stripped) is byte-identical
    # and rebuilds to the identical CID — the proof touched no signed/canonical bytes.
    carried = _strip_envelope(stamped)
    assert carried == request
    again = knit.build(
        from_pub=sender_pub, to_pub=recv, symbol="PLS", amount=42,
        from_nonce=7, timestamp=0,
    )
    assert again.id == cid_before
    assert canonical.encode(again.to_record()) == canonical.encode(a_knit.to_record())


@pytest.mark.property
def test_stamping_a_proof_leaves_the_business_payload_untouched():
    """_stamp_id_proof never mutates the business payload or its canonical bytes."""
    node = FabricNode()  # always keyed → always stamps
    payload = {"kind": "fabric-sync-request"}
    before = canonical.encode(payload)
    stamped = node._stamp_id_proof(payload)
    # The proof rides only under the stripped envelope key; the original payload
    # object is untouched and its canonical bytes are unchanged once stripped.
    assert payload == {"kind": "fabric-sync-request"}
    assert canonical.encode(payload) == before
    assert ENVELOPE_ID_PROOF_KEY in stamped
    assert _strip_envelope(stamped) == payload


@pytest.mark.property
def test_relay_carrier_strips_the_proof_and_keeps_mailbox_keying():
    """Proven-identity keying is a TCP concern (the NAT collateral-ban is specific
    to ``tcp:<ip>``). The relay carrier keeps keying on the per-node-stable reply
    mailbox: it strips the proof envelope key, so a relay request keeps its
    pre-#58 mailbox-keyed ban behaviour byte-for-byte unchanged."""
    from knitweb.p2p.relay import RelayTransport, _strip_envelope, relay_peer_id

    # The relay carrier strips every _relay_* key (including the proof) before
    # stamping the mailbox id, so the proof never reaches dispatch over the relay.
    priv, _ = crypto.generate_keypair()
    carried = {"kind": "fabric-sync-request"}
    framed = dict(carried)
    framed[ENVELOPE_ID_PROOF_KEY] = _proof_envelope(priv, timestamp=1)
    framed["_relay_reply_to"] = "srv-mb"
    framed["_relay_rid"] = 1
    assert _strip_envelope(framed) == carried  # proof dropped with the envelope

    # A banned mailbox is still refused over the relay even though a proof rode
    # along: the relay path never upgrades to the node key.
    transport = RelayTransport(base_url="https://5mart.ml", mailbox="srv", poster=None)
    assert transport.tag == "relay"
    node = FabricNode(transport=transport)
    node.reputation.penalize(relay_peer_id("evil-mb"), Offense.EQUIVOCATION)
    # The relay carrier hands dispatch the mailbox id only (no proof key), so the
    # mailbox ban gate fires exactly as before #58.
    out = run(node._dispatch(
        {"kind": "fabric-sync-request", ENVELOPE_PEER_KEY: relay_peer_id("evil-mb")}
    ))
    assert out == {"kind": "error", "code": "banned", "message": "peer is banned"}


@pytest.mark.property
def test_keyless_asyncio_node_dials_without_a_proof():
    """An account-less AsyncioP2PNode is keyless → no proof, pre-#58 behaviour."""
    keyless = AsyncioP2PNode()  # no account
    assert keyless._id_signing_key() is None
    assert keyless._stamp_id_proof({"kind": "x"}) == {"kind": "x"}  # unchanged
    # A node WITH an account stamps a proof keyed on its account pubkey.
    acct = AccountNode()
    keyed = AsyncioP2PNode(account=acct)
    assert keyed._id_signing_key() == acct.priv
    stamped = keyed._stamp_id_proof({"kind": "x"})
    proof = identity.id_proof_from_record(stamped[ENVELOPE_ID_PROOF_KEY])
    assert proof is not None and proof.pubkey == acct.pub
