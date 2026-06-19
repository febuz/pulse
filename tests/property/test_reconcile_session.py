"""Proofs for the carrier-oriented :class:`ReconcileSession` + the inv-recon codec.

The pure range-bisection state machine (:class:`knitweb.p2p.reconcile.Reconciler`)
is proven in ``test_reconcile.py``. This suite pins the thin lifecycle wrapper that
slices that bisection into one-request/one-response carrier batches — the shape the
live :class:`~knitweb.fabric.node.FabricNode` Erlay activation (#60) drives — and
the ``inv-recon-*`` envelope codec that carries each batch:

  * **session == pair** — driving a :class:`ReconcileSession` round-by-round (the
    initiator opens + advances, the responder responds) converges on the EXACT
    same symmetric difference as the reference :func:`reconcile_pair` driver;
  * **O(diff)** — a tiny diff over a large shared inventory converges in a handful
    of carrier rounds, NOT a number that scales with the inventory;
  * **identical ⇒ one round, zero CIDs** — an identical CID set prunes at the root
    in a single round and the initiator's missing set is empty;
  * **deterministic** — replaying the same two sets yields the identical round
    count and the identical learned difference (lexical order + integer XOR only);
  * **byte-identity sacred** — only reconcile frames (range summaries / CID lists)
    ever ride an ``inv-recon-*`` envelope; no record body, so a fresh Knit's CID is
    byte-for-byte unchanged (asserted directly), and the envelope round-trips its
    inner frame bytes verbatim.
"""

import hashlib

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit as knit_mod
from knitweb.p2p import wire
from knitweb.p2p.inventory import (
    RECON_RANGE,
    RECON_REQ,
    RECON_RESULT,
    InventoryError,
    build_recon_frame,
    parse_recon_frame,
)
from knitweb.p2p.reconcile import (
    RECONCILE_PROBE,
    ReconcileSession,
    build_probe_frame,
    reconcile_pair,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _cid(i: int) -> str:
    return "b" + hashlib.sha256(str(i).encode()).hexdigest()[:40]


def _cids(spec) -> list:
    return [_cid(i) for i in spec]


def _fresh_knit_record():
    priv, pub = crypto.generate_keypair()
    _priv2, pub2 = crypto.generate_keypair()
    knit = knit_mod.Knit(
        from_pub=pub,
        to_pub=pub2,
        symbol="PLS",
        amount=1000,
        from_nonce=0,
        timestamp=1,
        network=1,
    )
    record = wire.knit_to_record(knit)
    return record, canonical.cid(record)


def _drive_session(a_cids, b_cids, **kw):
    """Drive a ReconcileSession round-by-round like the live one-shot carrier.

    The initiator (a) opens and advances; the responder (b) is fed each batch via
    ``respond`` — exactly the FabricNode reconcile dial loop, minus the socket.
    Returns ``(a_missing, b_missing, rounds)``.
    """
    init = ReconcileSession(a_cids, **kw)
    resp = ReconcileSession(b_cids, **kw)
    batch = init.open()
    rounds = 0
    while batch and not init.done:
        rounds += 1
        reply = resp.respond(batch)
        batch = init.advance(reply)
    return set(init.missing), set(resp.missing), rounds


# ── 1. the inv-recon envelope codec round-trips a batch verbatim ──────────────

def test_recon_envelope_roundtrips_inner_frames_verbatim():
    probe = build_probe_frame("a", "z", 3, 7, 0)
    leaf_like = build_probe_frame("a", "m", 1, 5, 1)
    env = build_recon_frame(RECON_REQ, [probe, leaf_like])
    # It IS a standard wire frame (length prefix + canonical CBOR).
    assert wire.read_frame_bytes(env)["kind"] == RECON_REQ
    kind, frames = parse_recon_frame(env)
    assert kind == RECON_REQ
    # The inner reconcile frame bytes are carried byte-for-byte unchanged.
    assert frames == [probe, leaf_like]
    # And the inner probe still parses to its exact summary.
    assert wire.read_frame_bytes(frames[0])["kind"] == RECONCILE_PROBE


def test_recon_envelope_allows_empty_batch_for_convergence_signal():
    # An empty RECON_RESULT is exactly how the responder signals convergence.
    env = build_recon_frame(RECON_RESULT, [])
    kind, frames = parse_recon_frame(env)
    assert kind == RECON_RESULT and frames == []


def test_recon_envelope_rejects_bad_kind_and_payload():
    with pytest.raises(InventoryError):
        build_recon_frame("inv-announce", [])  # not a reconcile envelope kind
    with pytest.raises(InventoryError):
        build_recon_frame(RECON_REQ, [b"ok", 123])  # type: ignore[list-item]
    # Parsing a non-recon frame is rejected.
    bad = wire.write_frame_bytes({"kind": "inv-announce", "cids": []})
    with pytest.raises(InventoryError):
        parse_recon_frame(bad)


# ── 2. session converges on the EXACT symmetric difference (== reconcile_pair) ─

@pytest.mark.parametrize(
    "a_spec,b_spec",
    [
        (range(2000), range(2000)),                                  # identical
        (list(range(2000)) + list(range(900000, 900005)),
         list(range(2000)) + list(range(800000, 800007))),          # small diff
        (range(40), range(100, 140)),                               # disjoint
        ([], range(300)),                                           # one empty
        ([], []),                                                   # both empty
    ],
)
def test_session_converges_like_reconcile_pair(a_spec, b_spec):
    a = _cids(a_spec)
    b = _cids(b_spec)
    a_missing, b_missing, _ = _drive_session(a, b)
    sa, sb = set(a), set(b)
    # The initiator learns EXACTLY what it lacks; so does the responder.
    assert a_missing == sb - sa
    assert b_missing == sa - sb
    # And it matches the reference all-at-once driver byte-for-byte in OUTCOME.
    ref = reconcile_pair(a, b)
    assert a_missing == set(ref["a_missing"])
    assert b_missing == set(ref["b_missing"])


def test_session_subset_initiator_learns_only_its_missing():
    full = _cids(range(500))
    a_missing, b_missing, _ = _drive_session(full[:480], full)
    assert a_missing == set(full[480:])  # exactly the 20 it lacks
    assert b_missing == set()            # responder lacks nothing


# ── 3. O(diff): rounds scale with the diff, NOT the inventory ─────────────────

def test_identical_sets_are_one_round_zero_missing():
    common = _cids(range(3000))
    a_missing, b_missing, rounds = _drive_session(common, common)
    assert a_missing == set() and b_missing == set()
    # An identical inventory prunes at the root: a single carrier round, no leaves.
    assert rounds == 1


def test_rounds_scale_with_diff_not_inventory():
    # Same tiny diff over a 200-CID inventory and over a 6000-CID inventory: the
    # round count must NOT blow up with the inventory size — that is the O(diff)
    # property the whole activation exists for.
    def rounds_for(inv_size, diff):
        base = _cids(range(inv_size))
        a = base + _cids(range(900000, 900000 + diff))
        b = base + _cids(range(800000, 800000 + diff))
        return _drive_session(a, b)[2]

    small_inv = rounds_for(200, 2)
    huge_inv = rounds_for(6000, 2)
    # A 30x larger inventory with the SAME 2-each diff stays within a small
    # constant factor (bisection depth grows only ~log of the keyspace, not the
    # inventory) — emphatically NOT 30x more rounds.
    assert huge_inv <= small_inv + 6


# ── 4. determinism: identical rounds + learned diff on replay ─────────────────

def test_session_is_deterministic():
    common = _cids(range(800))
    a = common + _cids(range(300000, 300005))
    b = common + _cids(range(200000, 200005))
    r1 = _drive_session(a, b)
    r2 = _drive_session(a, b)
    assert r1 == r2  # (a_missing, b_missing, rounds) identical


def test_session_done_flips_and_blocks_further_advance():
    a = _cids(range(50))
    init = ReconcileSession(a, max_rounds=5)
    resp = ReconcileSession(_cids(range(50)))  # identical -> root prune
    batch = init.open()
    reply = resp.respond(batch)
    batch = init.advance(reply)
    assert init.done is True
    assert batch == []


# ── 5. byte-identity sacred: reconcile moves only CIDs over the envelope ──────

def test_session_carries_only_cids_and_preserves_knit_cid():
    record, cid = _fresh_knit_record()
    a = _cids(range(100))
    b = _cids(range(100)) + [cid]
    a_missing, _, _ = _drive_session(a, b)
    # The initiator's missing set is exactly the fresh Knit's CID.
    assert a_missing == {cid}
    (only,) = a_missing
    assert only == canonical.cid(record)  # re-derives byte-identically
    # No record body / signature ever appears in an inv-recon envelope: it carries
    # only opaque reconcile frames, whose payloads are flat CID-string lists.
    env = build_recon_frame(RECON_RANGE, [build_probe_frame("a", "z", 1, 9, 0)])
    msg = wire.read_frame_bytes(env)
    assert msg["kind"] == RECON_RANGE
    assert "record" not in msg and "sig" not in msg and "from_sig" not in msg
