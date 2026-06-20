"""Lazy gossip honours the peer-score gate — a negative-scored peer is cut off.

``on_graft`` already refuses a peer whose score is negative ("gossipsub's primary
spam/sybil defence"), but until now ``on_ihave``/``on_iwant`` served *any* peer. That
let a peer we had already penalised keep inducing work: its IHAVE still drew an IWANT
(→ body fetches) and its IWANT was still served (→ bandwidth). These tests pin the
gossip-threshold gate: a negative-scored peer gets no IWANT and is served no ids, while
fresh (score-0) and positive peers are unaffected. Integer score, no clock/rand; the
gate returns early so no wire frame or canonical/CID byte changes.
"""
from knitweb.p2p.mesh import Gossipsub, build_ihave_frame, build_iwant_frame, parse_iwant_frame

TOPIC = "web/demo"


def _gs():
    import random
    return Gossipsub(rng=random.Random(0))


def test_on_ihave_ignored_from_negative_scored_peer():
    gs = _gs()
    gs.record_invalid("bad", "x")            # default w_invalid=-50 -> score -50
    assert gs.score_of("bad") < 0
    # An IHAVE advertising ids we lack would normally draw an IWANT…
    frame = build_ihave_frame(TOPIC, ["m1", "m2"])
    assert gs.on_ihave("bad", frame) is None     # …but the penalised peer is ignored
    # Control: a fresh (score-0) peer with the same frame IS served an IWANT.
    reply = gs.on_ihave("good", frame)
    assert reply is not None
    assert parse_iwant_frame(reply) == ["m1", "m2"]


def test_on_iwant_not_served_to_negative_scored_peer():
    gs = _gs()
    gs.record_delivery("src", TOPIC, "m1")   # we now hold m1
    gs.record_invalid("bad", "x")
    assert gs.score_of("bad") < 0
    assert gs.on_iwant("bad", build_iwant_frame(["m1"])) == []   # penalised peer unserved
    # Control: a fresh peer wanting the same held id IS served it.
    assert gs.on_iwant("good", build_iwant_frame(["m1"])) == ["m1"]


def test_gossip_gate_is_strict_zero_score_still_served():
    # The gate is `< 0`, not `<= 0`: a peer sitting at exactly 0 is still a full
    # gossip participant. Land one on 0 — 25 first-deliveries (+2 each = +50) cancel
    # one invalid (-50).
    gs = _gs()
    for k in range(25):
        gs.record_delivery("p", TOPIC, f"r{k}")   # 25 * 2 = +50
    gs.record_invalid("p", "x")                   # -50  -> net 0
    assert gs.score_of("p") == 0
    # Score-0 peer is served gossip (gate is strict on negative only).
    assert gs.on_iwant("p", build_iwant_frame(["r0"])) == ["r0"]
    assert gs.on_ihave("p", build_ihave_frame(TOPIC, ["brand_new"])) is not None
