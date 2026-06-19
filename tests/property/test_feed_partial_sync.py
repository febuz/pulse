"""Property/parity tests for partial feed sync over the p2p wire (#24).

These pin the wire-glue that connects the frozen multiproof primitives
(``feed_multiproof.prove_range`` / ``verify_range_multiproof``) to the node:

* the ``RangeMultiProof`` wire map round-trips byte-identically through
  canonical CBOR (the signed-record byte-identity gate must stay intact —
  the head bytes are never re-derived, only carried);
* ``_serve_feed`` populates ``merkle_nodes`` with a proof a peer can verify
  against the *signed* head, with no full log on the wire;
* a tampered slice / forged proof is rejected exactly as the primitive rejects it.

No floats, integers only, and the head's signed bytes are passed through verbatim.
"""

from dataclasses import replace

import pytest

from knitweb.core import canonical, crypto
from knitweb.fabric.feed import Feed
from knitweb.fabric.feed_multiproof import prove_range, verify_range_multiproof
from knitweb.p2p.node import AsyncioP2PNode
from knitweb.p2p.wire import (
    feed_head_from_record,
    multiproof_from_record,
    multiproof_to_record,
)

pytestmark = pytest.mark.property


def _feed(n, tag="entry"):
    priv, _ = crypto.generate_keypair()
    f = Feed(priv)
    for i in range(n):
        f.append({"i": i, "payload": f"{tag}-{i}"})
    return f


# ── multiproof wire round-trip is exact and canonical ────────────────────────

def test_multiproof_record_round_trips():
    f = _feed(17)
    for start in range(f.length):
        for count in range(1, f.length - start + 1):
            proof = prove_range(f.entries, start, count)
            back = multiproof_from_record(multiproof_to_record(proof))
            assert back == proof


def test_multiproof_record_is_canonical_byte_identity():
    f = _feed(11)
    proof = prove_range(f.entries, 3, 4)
    record = multiproof_to_record(proof)
    # Re-encoding the decoded map yields identical bytes — canonical CBOR is a
    # bijection here, so siblings/ints survive a round-trip unchanged.
    raw = canonical.encode(record)
    assert canonical.encode(canonical.decode(raw)) == raw
    assert multiproof_from_record(canonical.decode(raw)) == proof


# ── _serve_feed emits a verifiable proof; the head bytes are untouched ───────

def test_serve_range_emits_verifiable_multiproof():
    f = _feed(20)
    node = AsyncioP2PNode()
    node.add_feed(f)
    start, count = 5, 6
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": start, "count": count}
    )
    assert out["kind"] == "feed-data"
    # The served head is a valid signed commitment over the same root/length/fork.
    # (The sig itself is freshly minted per head() call — ECDSA k is random — so we
    # check the signed *content*, the bytes that authority binds to, is identical.)
    served = feed_head_from_record(out["head"])
    assert served.verify()
    assert served.signable() == f.head().signable()
    assert (served.feed, served.root, served.length, served.fork) == (
        f.feed,
        f.root(),
        f.length,
        f.fork,
    )
    entries = out["entries"]
    assert entries == [f.entry(start + j) for j in range(count)]
    proof = multiproof_from_record(out["merkle_nodes"])
    assert verify_range_multiproof(served, entries, proof)
    # bandwidth: the slice + proof are far smaller than the whole log
    assert len(proof.siblings) < f.length


def test_serve_full_feed_keeps_empty_merkle_nodes():
    f = _feed(8)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": 0, "count": None}
    )
    assert out["merkle_nodes"] == []
    assert out["entries"] == f.entries


# ── serve-side range guards ──────────────────────────────────────────────────

def test_serve_out_of_bounds_range_errors():
    f = _feed(6)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": 4, "count": 5}
    )
    assert out["kind"] == "error"
    assert out["code"] == "unsupported-range"


def test_serve_nonpositive_count_errors():
    f = _feed(6)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": 0, "count": 0}
    )
    assert out["kind"] == "error"


def test_serve_bool_start_rejected():
    f = _feed(6)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": True, "count": 2}
    )
    assert out["kind"] == "error"
    assert out["code"] == "bad-request"


# ── verify-side rejects tampering exactly like the primitive ─────────────────

def test_slice_from_message_rejects_tampered_entry():
    f = _feed(12)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": 3, "count": 4}
    )
    out["entries"][1] = {"i": 4, "payload": "TAMPERED"}
    from knitweb.p2p.node import P2PError

    with pytest.raises(P2PError):
        node._slice_from_message(out, 3, 4)


def test_slice_from_message_rejects_proof_range_mismatch():
    f = _feed(12)
    node = AsyncioP2PNode()
    node.add_feed(f)
    out = node._serve_feed(
        {"kind": "feed-request", "feed": f.feed, "start": 3, "count": 4}
    )
    # forge the carried proof to claim a different start
    bad = multiproof_from_record(out["merkle_nodes"])
    out["merkle_nodes"] = multiproof_to_record(replace(bad, start=2))
    from knitweb.p2p.node import P2PError

    with pytest.raises(P2PError):
        node._slice_from_message(out, 3, 4)
