"""Proofs for the gossipsub mesh — bounded eager-push + lazy IHAVE/IWANT + score.

The mesh module ports libp2p gossipsub v1.1's two-tier dissemination graph to the
knitweb wire: a bounded eager-push mesh maintained around integer D/D_low/D_high
via GRAFT/PRUNE, lazy IHAVE/IWANT message-id gossip that defers body fetch to the
inventory getdata path, and a compact integer peer-score picking GRAFT/PRUNE
candidates. These tests pin the properties the module provides:

  * **bounded mesh** — after a heartbeat the mesh degree stays within
    [d_low, d_high] (and never exceeds d_high) whenever enough eligible peers
    exist; below d_low it grafts toward d, above d_high it prunes toward d;
  * **IWANT only fetches missing** — on_ihave requests exactly the advertised
    ids the node does not already hold, never bodies;
  * **score gates membership** — a negative-scored peer is refused grafting both
    by the heartbeat and by on_graft; higher scores are preferred for grafting,
    lower scores pruned first;
  * **only ids travel** — every control frame is ids/topic, never a record body,
    so no signed record is re-encoded and CID byte-identity is untouched;
  * **deterministic** — integer epoch + injected RNG only; same seed -> same mesh.
"""

import random

import pytest

from knitweb.core import canonical, crypto
from knitweb.ledger import knit as knit_mod
from knitweb.p2p import wire
from knitweb.p2p.inventory import InventoryRelay, build_getdata_frame
from knitweb.p2p.mesh import (
    IHAVE,
    IWANT,
    MAX_IDS_PER_FRAME,
    Gossipsub,
    MeshError,
    MeshParams,
    PeerScore,
    ScoreParams,
    build_graft_frame,
    build_ihave_frame,
    build_iwant_frame,
    build_prune_frame,
    parse_graft_frame,
    parse_ihave_frame,
    parse_iwant_frame,
    parse_prune_frame,
)


# ── helpers ──────────────────────────────────────────────────────────────────

TOPIC = "web/demo"


def _gs(seed=0, **kw):
    return Gossipsub(rng=random.Random(seed), **kw)


def _add_peers(gs, topic, n, prefix="p"):
    peers = [f"{prefix}{i}" for i in range(n)]
    for p in peers:
        gs.add_peer(topic, p)
    return peers


def _fresh_knit_record():
    priv, pub = crypto.generate_keypair()
    _priv2, pub2 = crypto.generate_keypair()
    knit = knit_mod.Knit(
        from_pub=pub, to_pub=pub2, symbol="PLS", amount=1000,
        from_nonce=0, timestamp=1, network=1,
    )
    record = wire.knit_to_record(knit)
    return record, canonical.cid(record)


# ── 1. frame codec round-trips, ids-only, bounded ────────────────────────────

def test_frame_roundtrips():
    assert parse_graft_frame(build_graft_frame(TOPIC)) == TOPIC
    assert parse_prune_frame(build_prune_frame(TOPIC)) == TOPIC
    topic, ids = parse_ihave_frame(build_ihave_frame(TOPIC, ["a", "b", "a"]))
    assert topic == TOPIC and ids == ["a", "b", "a"]
    assert parse_iwant_frame(build_iwant_frame(["x", "y"])) == ["x", "y"]


def test_frames_carry_only_ids_no_body():
    """Decoding any control frame yields ids/topic — never a record body."""
    record, cid = _fresh_knit_record()
    for frame in (
        build_ihave_frame(TOPIC, [cid]),
        build_iwant_frame([cid]),
    ):
        msg = wire.read_frame_bytes(frame)
        # The only payload keys are kind/topic/ids — no 'from'/'sig'/'amount'.
        assert set(msg) <= {"kind", "topic", "ids"}
        assert "from" not in msg and "sig" not in msg


def test_frame_kind_mismatch_rejected():
    with pytest.raises(MeshError):
        parse_graft_frame(build_prune_frame(TOPIC))
    with pytest.raises(MeshError):
        parse_ihave_frame(build_iwant_frame(["a"]))


def test_frame_validation_rejects_bad_input():
    with pytest.raises(MeshError):
        build_graft_frame("")
    with pytest.raises(MeshError):
        build_ihave_frame(TOPIC, ["ok", ""])
    with pytest.raises(MeshError):
        build_iwant_frame(["x", 5])  # non-str id


# ── 2. bounded mesh: heartbeat keeps degree in [d_low, d_high] ────────────────

def test_heartbeat_grafts_up_to_d_when_below_d_low():
    gs = _gs(params=MeshParams(d=6, d_low=4, d_high=12))
    _add_peers(gs, TOPIC, 20)
    assert gs.mesh_degree(TOPIC) == 0
    out = gs.heartbeat([TOPIC])
    # Grafted toward d (=6) since we were below d_low.
    assert gs.mesh_degree(TOPIC) == 6
    # Each grafted peer got exactly one GRAFT frame for this topic.
    grafted = gs.mesh_peers(TOPIC)
    assert set(out) == set(grafted)
    for peer in grafted:
        assert [parse_graft_frame(f) for f in out[peer]] == [TOPIC]


def test_heartbeat_prunes_down_to_d_when_above_d_high():
    gs = _gs(params=MeshParams(d=6, d_low=4, d_high=12))
    peers = _add_peers(gs, TOPIC, 20)
    # Force an oversized mesh directly via accepting GRAFTs (all positive score).
    for p in peers[:15]:
        assert gs.on_graft(p, build_graft_frame(TOPIC)) is None or True
    # on_graft refuses beyond d_high, so push past it by bypassing the gate:
    gs._mesh[TOPIC] = set(peers[:15])
    assert gs.mesh_degree(TOPIC) == 15
    out = gs.heartbeat([TOPIC])
    assert gs.mesh_degree(TOPIC) == 6  # pruned down to d
    pruned = [p for p in peers[:15] if p not in gs.mesh_peers(TOPIC)]
    assert set(out) == set(pruned)
    for peer in pruned:
        assert [parse_prune_frame(f) for f in out[peer]] == [TOPIC]


def test_mesh_stays_within_bounds_over_many_heartbeats():
    """The core invariant: degree never exceeds d_high and settles within band."""
    p = MeshParams(d=6, d_low=4, d_high=12)
    gs = _gs(seed=7, params=p)
    _add_peers(gs, TOPIC, 50)
    for _ in range(30):
        gs.heartbeat([TOPIC])
        deg = gs.mesh_degree(TOPIC)
        assert deg <= p.d_high
        assert p.d_low <= deg  # plenty of candidates -> always at/above d_low
    assert p.d_low <= gs.mesh_degree(TOPIC) <= p.d_high


def test_heartbeat_does_nothing_inside_band():
    p = MeshParams(d=6, d_low=4, d_high=12)
    gs = _gs(params=p)
    peers = _add_peers(gs, TOPIC, 20)
    gs._mesh[TOPIC] = set(peers[:6])  # exactly d, inside band
    out = gs.heartbeat([TOPIC])
    assert out == {}
    assert gs.mesh_degree(TOPIC) == 6


def test_heartbeat_advances_integer_epoch():
    gs = _gs()
    assert gs.epoch == 0
    gs.heartbeat([TOPIC])
    gs.heartbeat([TOPIC])
    assert gs.epoch == 2
    assert isinstance(gs.epoch, int)


# ── 3. peer-score gates and orders mesh membership ───────────────────────────

def test_negative_score_peer_refused_by_heartbeat():
    gs = _gs(params=MeshParams(d=3, d_low=2, d_high=6))
    good = _add_peers(gs, TOPIC, 2, prefix="g")
    bad = "bad0"
    gs.add_peer(TOPIC, bad)
    # Drive bad's score negative via invalid deliveries.
    for _ in range(2):
        gs.record_invalid(bad, "m")
    assert gs.score_of(bad) < 0
    gs.heartbeat([TOPIC])
    # Only the two good peers got grafted; the negative one was refused.
    assert bad not in gs.mesh_peers(TOPIC)
    assert set(gs.mesh_peers(TOPIC)) == set(good)


def test_on_graft_refuses_negative_score_with_prune():
    gs = _gs()
    gs.add_peer(TOPIC, "bad0")
    gs.record_invalid("bad0", "m")
    assert gs.score_of("bad0") < 0
    reply = gs.on_graft("bad0", build_graft_frame(TOPIC))
    assert reply is not None
    assert parse_prune_frame(reply) == TOPIC  # bounced
    assert "bad0" not in gs.mesh_peers(TOPIC)


def test_on_graft_accepts_known_nonnegative_peer():
    gs = _gs()
    gs.add_peer(TOPIC, "ok0")
    assert gs.on_graft("ok0", build_graft_frame(TOPIC)) is None
    assert "ok0" in gs.mesh_peers(TOPIC)
    # Idempotent.
    assert gs.on_graft("ok0", build_graft_frame(TOPIC)) is None
    assert gs.mesh_peers(TOPIC) == ["ok0"]


def test_on_graft_refuses_unknown_and_overfull():
    gs = _gs(params=MeshParams(d=2, d_low=1, d_high=2))
    # Unknown (never added) peer is refused.
    assert parse_prune_frame(gs.on_graft("ghost", build_graft_frame(TOPIC))) == TOPIC
    # Fill to d_high, then a further graft is refused.
    a, b, c = "a", "b", "c"
    for p in (a, b, c):
        gs.add_peer(TOPIC, p)
    assert gs.on_graft(a, build_graft_frame(TOPIC)) is None
    assert gs.on_graft(b, build_graft_frame(TOPIC)) is None
    assert parse_prune_frame(gs.on_graft(c, build_graft_frame(TOPIC))) == TOPIC


def test_graft_prefers_higher_score():
    gs = _gs(seed=1, params=MeshParams(d=2, d_low=2, d_high=4))
    hi = _add_peers(gs, TOPIC, 2, prefix="hi")
    lo = _add_peers(gs, TOPIC, 5, prefix="lo")
    # Reward the 'hi' peers with first deliveries.
    for p in hi:
        for k in range(3):
            gs.record_delivery(p, TOPIC, f"{p}-{k}")
    gs.heartbeat([TOPIC])
    # The two highest-scoring (hi) peers are picked over the lo peers.
    assert set(gs.mesh_peers(TOPIC)) == set(hi)


def test_prune_drops_lowest_score_first():
    gs = _gs(seed=3, params=MeshParams(d=2, d_low=1, d_high=3))
    peers = _add_peers(gs, TOPIC, 5)
    gs._mesh[TOPIC] = set(peers[:5])  # oversized
    # Make p0,p1 high-score; the rest stay at 0.
    for p in peers[:2]:
        for k in range(5):
            gs.record_delivery(p, TOPIC, f"{p}-{k}")
    gs.heartbeat([TOPIC])
    assert gs.mesh_degree(TOPIC) == 2
    # The two survivors are the highest-scoring p0,p1.
    assert set(gs.mesh_peers(TOPIC)) == {peers[0], peers[1]}


def test_score_is_integer_weighted_sum_and_time_capped():
    params = ScoreParams(w_time=1, time_cap=3, w_first=2, w_invalid=-50)
    s = PeerScore(epochs_in_mesh=10, first_message_deliveries=4, invalid_message_deliveries=1)
    # time capped at 3: 1*3 + 2*4 + (-50)*1 = 3 + 8 - 50 = -39
    val = s.value(params)
    assert val == -39
    assert isinstance(val, int)


def test_first_delivery_only_rewards_first_peer():
    gs = _gs()
    assert gs.record_delivery("p0", TOPIC, "m1") is True   # first
    assert gs.record_delivery("p1", TOPIC, "m1") is False  # not first
    assert gs.score_of("p0") > gs.score_of("p1")
    assert gs._score("p0").first_message_deliveries == 1
    assert gs._score("p1").first_message_deliveries == 0


# ── 4. lazy gossip: IWANT requests exactly the missing ids, never bodies ──────

def test_on_ihave_wants_only_missing_ids():
    gs = _gs()
    # We already hold m1 (delivered); m2,m3 are new.
    gs.record_delivery("p0", TOPIC, "m1")
    frame = build_ihave_frame(TOPIC, ["m1", "m2", "m3"])
    reply = gs.on_ihave("p0", frame)
    assert reply is not None
    assert parse_iwant_frame(reply) == ["m2", "m3"]  # only the missing ones


def test_on_ihave_returns_none_when_all_held():
    gs = _gs()
    gs.record_delivery("p0", TOPIC, "m1")
    gs.record_delivery("p0", TOPIC, "m2")
    assert gs.on_ihave("p0", build_ihave_frame(TOPIC, ["m1", "m2"])) is None


def test_on_ihave_dedups_within_frame():
    gs = _gs()
    reply = gs.on_ihave("p0", build_ihave_frame(TOPIC, ["m1", "m1", "m2"]))
    assert parse_iwant_frame(reply) == ["m1", "m2"]


def test_build_ihave_digest_and_limit():
    gs = _gs()
    for k in range(5):
        gs.record_delivery("p0", TOPIC, f"m{k}")
    frame = gs.build_ihave(TOPIC, limit=3)
    topic, ids = parse_ihave_frame(frame)
    assert topic == TOPIC
    # Most-recent first, capped at 3.
    assert ids == ["m4", "m3", "m2"]
    assert gs.build_ihave("empty/topic") is None


def test_on_iwant_returns_only_held_ids_for_inventory():
    gs = _gs()
    gs.record_delivery("p0", TOPIC, "m1")
    gs.record_delivery("p0", TOPIC, "m2")
    # Peer wants m1 (held), m2 (held), m9 (not held).
    out = gs.on_iwant("peer", build_iwant_frame(["m9", "m1", "m2"]))
    assert out == ["m1", "m2"]  # m9 omitted; sorted


def test_iwant_composes_with_inventory_getdata():
    """on_iwant ids feed inventory getdata; the body travels there, not here."""
    # Inventory store holds the verbatim signed frame for a fresh Knit.
    record, cid = _fresh_knit_record()
    record_frame = wire.write_frame_bytes(record)
    store = {cid: record_frame}
    relay = InventoryRelay(lambda c: store.get(c))

    gs = _gs()
    # Mesh advertises the id only (no body).
    gs.record_delivery("p0", TOPIC, cid)
    # A peer that lacks it would IWANT it; here the *holder* answers an IWANT by
    # resolving ids -> inventory getdata, which returns the verbatim signed bytes.
    want_ids = gs.on_iwant("peer", build_iwant_frame([cid]))
    assert want_ids == [cid]
    bodies = relay.on_getdata(build_getdata_frame(want_ids))
    assert bodies == [record_frame]
    # Byte-identity preserved end to end through the inventory path.
    relayed = wire.read_frame_bytes(bodies[0])
    assert canonical.cid(relayed) == cid
    assert relayed == record


# ── 5. eager push of ids to mesh peers (bodies elsewhere) ─────────────────────

def test_publish_targets_mesh_peers_only():
    gs = _gs(params=MeshParams(d=3, d_low=2, d_high=6))
    peers = _add_peers(gs, TOPIC, 10)
    gs.heartbeat([TOPIC])
    mesh = set(gs.mesh_peers(TOPIC))
    targets = gs.publish(TOPIC, "newid")
    assert set(targets) == mesh  # only mesh peers, not all 10 candidates
    assert len(targets) <= gs.params.d_high


def test_publish_excludes_source_peer():
    gs = _gs()
    for p in ("a", "b", "c"):
        gs.add_peer(TOPIC, p)
        gs.on_graft(p, build_graft_frame(TOPIC))
    targets = gs.forward(TOPIC, "id", exclude=["b"])
    assert "b" not in targets
    assert set(targets) == {"a", "c"}


# ── 6. determinism: same seed + integer ticks -> identical mesh ───────────────

def test_deterministic_under_fixed_seed():
    def run():
        gs = Gossipsub(rng=random.Random(99), params=MeshParams(d=6, d_low=4, d_high=12))
        _add_peers(gs, TOPIC, 40)
        snaps = []
        for _ in range(10):
            gs.heartbeat([TOPIC])
            snaps.append(gs.mesh_peers(TOPIC))
        return snaps

    assert run() == run()  # byte-identical evolution


def test_no_floats_anywhere_in_scores_and_degrees():
    gs = _gs()
    _add_peers(gs, TOPIC, 8)
    gs.heartbeat([TOPIC])
    gs.record_delivery("p0", TOPIC, "m")
    gs.record_invalid("p1", "m")
    assert isinstance(gs.mesh_degree(TOPIC), int)
    assert isinstance(gs.score_of("p0"), int)
    assert isinstance(gs.score_of("p1"), int)
    assert isinstance(gs.epoch, int)


# ── 7. param validation ──────────────────────────────────────────────────────

def test_mesh_params_require_ordering():
    with pytest.raises(MeshError):
        MeshParams(d=2, d_low=5, d_high=10)  # d_low > d
    with pytest.raises(MeshError):
        MeshParams(d=6, d_low=4, d_high=3)   # d > d_high


def test_score_params_reject_positive_penalty():
    with pytest.raises(MeshError):
        ScoreParams(w_invalid=1)  # penalty must be <= 0


# ── 8. seen/have are bounded LRU caches; eviction re-opens IWANT ──────────────

def test_seen_cap_evicts_oldest_and_rewants_evicted_id():
    """seen_cap bounds _seen and _have via real LRU (popitem(last=False)).

    Recording more than seen_cap distinct deliveries keeps only the
    seen_cap most-recent ids; the oldest is evicted, and on_ihave re-WANTs
    that evicted id (it is no longer in _seen) while not re-wanting a
    still-seen id.
    """
    gs = _gs(seen_cap=3)
    # Five distinct deliveries m0..m4 (> seen_cap == 3).
    for k in range(5):
        gs.record_delivery("p0", TOPIC, f"m{k}")
    # Oldest two (m0, m1) evicted; the 3 most-recent retained.
    assert len(gs._seen) == 3
    assert len(gs._have[TOPIC]) == 3
    assert set(gs._seen) == {"m2", "m3", "m4"}
    # The evicted id is re-wanted; the still-seen id is not.
    reply = gs.on_ihave("p0", build_ihave_frame(TOPIC, ["m0", "m4"]))
    assert parse_iwant_frame(reply) == ["m0"]


def test_inbound_parse_rejects_oversized_id_list():
    """parse_ihave_frame / parse_iwant_frame enforce MAX_IDS_PER_FRAME inbound.

    Existing tests only exercise the build path; this pins the inbound guard
    in _check_id_list against a frame constructed directly via the wire codec.
    """
    too_many = ["x"] * (MAX_IDS_PER_FRAME + 1)
    ihave_bytes = wire.write_frame_bytes(
        {"kind": IHAVE, "topic": TOPIC, "ids": too_many}
    )
    iwant_bytes = wire.write_frame_bytes({"kind": IWANT, "ids": too_many})
    msg = "too many ids in one frame: 50001 > 50000"
    with pytest.raises(MeshError, match=msg):
        parse_ihave_frame(ihave_bytes)
    with pytest.raises(MeshError, match=msg):
        parse_iwant_frame(iwant_bytes)
