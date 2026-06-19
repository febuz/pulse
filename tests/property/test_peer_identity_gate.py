"""Property proofs for the identity-keyed connection gate.

The gate binds a *proven* secp256k1 identity to the reputation/ban ledger so a
ban follows the identity (``node:<pubkey>``), not the carrier socket/mailbox.
These tests pin the two security properties that buys, plus the safety
invariants that keep it adoptable:

  * **Sybil ban-evasion is closed** — a banned identity stays banned across an
    arbitrary carrier (IP/mailbox) rotation, as long as it keeps proving the same
    key. If it stops proving (or cannot prove a fresh key), it loses the proven
    accept it would otherwise have, so rotation buys it nothing.
  * **Collateral NAT/relay bans are removed** — two peers sharing one carrier are
    judged on their own proven keys; banning one leaves the other ACCEPTed.
  * **Carrier fallback is unchanged** — no/invalid/stale proof falls back to the
    carrier key, so every pre-gate peer behaves exactly as before.
  * **Byte-identity is untouched** — exercising the whole gate never changes a
    Knit's CID (the gate touches no canonical/signed-record bytes).

Pure and deterministic: no sockets, no ``await``, injected nonce + clock.
"""

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit
from knitweb.p2p import identity
from knitweb.p2p.peer_identity_gate import (
    GateDecision,
    IdentitySource,
    PeerIdentityGate,
)
from knitweb.p2p.reputation import Offense, PeerReputation

_NONCE = b"\x07" * identity.NONCE_LEN


def _gate(**kw) -> PeerIdentityGate:
    return PeerIdentityGate(PeerReputation(), **kw)


def _challenge_proof(priv: str, nonce: bytes = _NONCE):
    ch = identity.issue_challenge(nonce=nonce)
    return ch, identity.make_proof(ch, priv)


def _piggyback(priv: str, ts: int, nonce: bytes = _NONCE):
    return identity.make_id_proof(priv, nonce=nonce, timestamp=ts)


# ── 1. A verified challenge proof keys on the proven node id ─────────────────


@pytest.mark.property
def test_challenge_proof_keys_on_proven_pubkey():
    priv, pub = crypto.generate_keypair()
    gate = _gate()
    ch, proof = _challenge_proof(priv)
    v = gate.resolve("tcp:1.2.3.4", challenge=ch, proof=proof)
    assert v.proven
    assert v.source is IdentitySource.CHALLENGE
    assert v.pubkey == pub
    assert v.rep_key == identity.node_peer_id(pub)
    assert v.rep_key.startswith(identity.NODE_PEER_PREFIX)
    assert v.carrier_key == "tcp:1.2.3.4"
    assert v.accepted


@pytest.mark.property
def test_piggyback_proof_keys_on_proven_pubkey():
    priv, pub = crypto.generate_keypair()
    gate = _gate()
    proof = _piggyback(priv, ts=1000)
    v = gate.resolve("relay:mailbox-abc", proof=proof, now=1000)
    assert v.source is IdentitySource.PIGGYBACK
    assert v.rep_key == identity.node_peer_id(pub)
    assert v.accepted


# ── 2. SECURITY: a ban follows the identity across carrier rotation ──────────


@pytest.mark.property
def test_ban_follows_identity_across_ip_rotation():
    """The headline property: a Sybil cannot dodge its ban by rotating IPs."""
    priv, pub = crypto.generate_keypair()
    gate = _gate()

    # Earn a one-shot ban on the proven identity from one carrier.
    ch, proof = _challenge_proof(priv)
    v = gate.resolve("tcp:10.0.0.1", challenge=ch, proof=proof)
    v = gate.penalize(v, Offense.EQUIVOCATION)  # 100 == instant ban
    assert v.decision is GateDecision.REJECT
    assert gate.is_banned(v)

    # Reconnect from a *completely different* IP, still proving the same key
    # (fresh nonce/challenge — a real Sybil reconnection).
    ch2, proof2 = _challenge_proof(priv, nonce=b"\x42" * identity.NONCE_LEN)
    v2 = gate.resolve("tcp:203.0.113.99", challenge=ch2, proof=proof2)
    assert v2.rep_key == identity.node_peer_id(pub)
    assert v2.decision is GateDecision.REJECT  # ban followed the identity
    assert not v2.accepted


@pytest.mark.property
def test_ban_follows_identity_across_carrier_kind_change():
    """Even switching carrier *kind* (tcp -> relay) does not shed the ban."""
    priv, _ = crypto.generate_keypair()
    gate = _gate()
    p1 = _piggyback(priv, ts=500)
    v = gate.penalize(gate.resolve("tcp:10.0.0.1", proof=p1, now=500), Offense.FEED_CONFLICT)
    assert not v.accepted
    p2 = _piggyback(priv, ts=600, nonce=b"\x99" * identity.NONCE_LEN)
    v2 = gate.resolve("relay:some-mailbox", proof=p2, now=600)
    assert v2.decision is GateDecision.REJECT


@pytest.mark.property
def test_unproven_rotation_loses_the_proven_accept():
    """A Sybil that *stops* proving cannot inherit a clean carrier silently:
    it drops to a carrier key and is judged there (here: clean), but it has lost
    the proven-identity standing — the ban on its key is intact and re-provable.
    """
    priv, pub = crypto.generate_keypair()
    gate = _gate()
    ch, proof = _challenge_proof(priv)
    gate.penalize(gate.resolve("tcp:10.0.0.1", challenge=ch, proof=proof), Offense.EQUIVOCATION)
    # The identity key is and stays banned regardless of carrier games.
    assert gate.reputation.is_banned(identity.node_peer_id(pub))
    # If it reconnects with no proof from a fresh IP it is only a carrier peer
    # (clean), but the moment it proves its key again it is rejected.
    ch2, proof2 = _challenge_proof(priv, nonce=b"\x11" * identity.NONCE_LEN)
    assert gate.resolve("tcp:9.9.9.9", challenge=ch2, proof=proof2).decision is GateDecision.REJECT


# ── 3. SECURITY: no collateral ban for an honest peer sharing a carrier ──────


@pytest.mark.property
def test_no_collateral_ban_for_shared_carrier():
    """Two proven peers behind ONE NAT egress IP: banning one spares the other."""
    bad_priv, bad_pub = crypto.generate_keypair()
    good_priv, good_pub = crypto.generate_keypair()
    gate = _gate()
    shared = "tcp:198.51.100.7"  # same NAT egress for both

    ch_b, p_b = _challenge_proof(bad_priv, nonce=b"\x01" * identity.NONCE_LEN)
    vb = gate.penalize(gate.resolve(shared, challenge=ch_b, proof=p_b), Offense.EQUIVOCATION)
    assert vb.decision is GateDecision.REJECT

    ch_g, p_g = _challenge_proof(good_priv, nonce=b"\x02" * identity.NONCE_LEN)
    vg = gate.resolve(shared, challenge=ch_g, proof=p_g)
    assert vg.rep_key == identity.node_peer_id(good_pub)
    assert vg.rep_key != identity.node_peer_id(bad_pub)
    assert vg.decision is GateDecision.ACCEPT  # honest neighbour untouched


# ── 4. Carrier fallback — unchanged behavior for unproven peers ──────────────


@pytest.mark.property
def test_no_proof_falls_back_to_carrier():
    gate = _gate()
    v = gate.resolve("tcp:1.2.3.4")
    assert v.source is IdentitySource.CARRIER
    assert not v.proven
    assert v.rep_key == "tcp:1.2.3.4"
    assert v.pubkey is None
    assert v.accepted


@pytest.mark.property
def test_forged_challenge_proof_falls_back_to_carrier():
    priv, pub = crypto.generate_keypair()
    gate = _gate()
    ch, good = _challenge_proof(priv)
    flipped = "00" if good.sig[:2] != "00" else "01"
    tampered = identity.Proof(pubkey=pub, sig=flipped + good.sig[2:])
    v = gate.resolve("tcp:1.2.3.4", challenge=ch, proof=tampered)
    assert v.source is IdentitySource.CARRIER
    assert v.rep_key == "tcp:1.2.3.4"


@pytest.mark.property
def test_proof_replayed_against_wrong_challenge_falls_back():
    priv, _ = crypto.generate_keypair()
    gate = _gate()
    issued, proof = _challenge_proof(priv, nonce=b"\xaa" * identity.NONCE_LEN)
    other = identity.issue_challenge(nonce=b"\xbb" * identity.NONCE_LEN)
    v = gate.resolve("tcp:1.2.3.4", challenge=other, proof=proof)
    assert v.source is IdentitySource.CARRIER


@pytest.mark.property
def test_stale_piggyback_proof_falls_back_to_carrier():
    priv, _ = crypto.generate_keypair()
    gate = _gate(proof_window_s=60)
    proof = _piggyback(priv, ts=1000)
    # now far outside the freshness window
    v = gate.resolve("relay:mb", proof=proof, now=1000 + 61)
    assert v.source is IdentitySource.CARRIER
    assert v.rep_key == "relay:mb"


@pytest.mark.property
def test_future_dated_piggyback_proof_falls_back():
    priv, _ = crypto.generate_keypair()
    gate = _gate(proof_window_s=60)
    proof = _piggyback(priv, ts=1000)
    v = gate.resolve("relay:mb", proof=proof, now=1000 - 61)
    assert v.source is IdentitySource.CARRIER


@pytest.mark.property
def test_challenge_proof_without_challenge_falls_back():
    priv, _ = crypto.generate_keypair()
    gate = _gate()
    _, proof = _challenge_proof(priv)
    # Caller forgot to pass the matching challenge — must NOT credit the proof.
    v = gate.resolve("tcp:1.2.3.4", proof=proof)
    assert v.source is IdentitySource.CARRIER


# ── 5. Carrier peers can still be banned on their carrier key ────────────────


@pytest.mark.property
def test_carrier_peer_still_bannable():
    gate = _gate()
    v = gate.resolve("tcp:1.2.3.4")
    v = gate.penalize(v, Offense.EQUIVOCATION)
    assert v.decision is GateDecision.REJECT
    # And a re-resolve on the same carrier sees the ban.
    assert gate.resolve("tcp:1.2.3.4").decision is GateDecision.REJECT


# ── 6. Determinism & input validation ────────────────────────────────────────


@pytest.mark.property
def test_resolve_is_deterministic():
    priv, _ = crypto.generate_keypair()
    g1, g2 = _gate(), _gate()
    ch1, p1 = _challenge_proof(priv)
    ch2, p2 = _challenge_proof(priv)
    v1 = g1.resolve("tcp:1.2.3.4", challenge=ch1, proof=p1)
    v2 = g2.resolve("tcp:1.2.3.4", challenge=ch2, proof=p2)
    assert (v1.rep_key, v1.source, v1.decision) == (v2.rep_key, v2.source, v2.decision)


@pytest.mark.property
def test_injected_nonce_source_drives_challenge():
    seen = []

    def src():
        seen.append(1)
        return b"\x5a" * identity.NONCE_LEN

    gate = _gate(nonce_source=src)
    ch = gate.new_challenge()
    assert ch.nonce == b"\x5a" * identity.NONCE_LEN
    assert seen == [1]


@pytest.mark.property
def test_piggyback_requires_now():
    priv, _ = crypto.generate_keypair()
    gate = _gate()
    proof = _piggyback(priv, ts=1000)
    with pytest.raises(ValueError):
        gate.resolve("relay:mb", proof=proof)  # no now=


@pytest.mark.property
def test_empty_carrier_rejected():
    gate = _gate()
    with pytest.raises(TypeError):
        gate.resolve("")


@pytest.mark.property
def test_gate_rejects_non_reputation():
    with pytest.raises(TypeError):
        PeerIdentityGate(object())  # type: ignore[arg-type]


# ── 7. Byte-identity invariant: the gate never changes a Knit CID ────────────


@pytest.mark.property
def test_gate_does_not_change_knit_cid():
    """A fresh Knit's CID is sacred and independent of any gate activity."""
    priv, pub = crypto.generate_keypair()
    _, recipient = crypto.generate_keypair()
    k = knit.build(pub, recipient, "PLS", 7, from_nonce=1, timestamp=123)
    cid_before = k.id
    encoded_before = canonical.encode(k.to_record())

    # Run the full gate lifecycle, including penalize/ban, on the same key.
    gate = _gate()
    ch, proof = _challenge_proof(priv)
    v = gate.resolve("tcp:1.2.3.4", challenge=ch, proof=proof)
    gate.penalize(v, Offense.EQUIVOCATION)
    gate.resolve("relay:mb", proof=_piggyback(priv, ts=10), now=10)

    # Re-build the identical Knit: its CID and canonical bytes are unchanged.
    k2 = knit.build(pub, recipient, "PLS", 7, from_nonce=1, timestamp=123)
    assert k2.id == cid_before
    assert canonical.encode(k2.to_record()) == encoded_before
