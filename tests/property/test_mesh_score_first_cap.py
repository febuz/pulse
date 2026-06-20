"""First-message-delivery reward is capped — a peer cannot mine unbounded mesh score.

``PeerScore.value()`` caps the time-in-mesh term but, until now, left
``first_message_deliveries`` uncapped — so a peer that is merely first to deliver many
message-ids accrued unbounded positive score and could outweigh ANY amount of
invalid-message penalty, defeating the integer score model that is gossipsub's only
sybil/DoS lever. ``value()`` now caps it at ``ScoreParams.first_cap`` (gossipsub's
FirstMessageDeliveriesCap). Integer-only, deterministic; touches no canonical/CID path.
"""
import random

import pytest

from knitweb.p2p.mesh import Gossipsub, MeshError, PeerScore, ScoreParams


def test_first_delivery_contribution_is_capped():
    params = ScoreParams(w_time=0, time_cap=0, w_first=2, first_cap=3, w_invalid=-50)
    s = PeerScore(first_message_deliveries=100)      # far above the cap
    assert s.value(params) == 2 * 3                  # min(100, 3) == 3, NOT 100


def test_cap_is_a_noop_below_the_cap():
    # an honest peer under the cap is scored exactly as before — no regression
    params = ScoreParams(w_time=0, time_cap=0, w_first=2, first_cap=100, w_invalid=-50)
    assert PeerScore(first_message_deliveries=4).value(params) == 2 * 4


def test_invalid_penalty_is_decisive_against_unbounded_first_deliveries():
    # The security property: even a peer that mined a million first-deliveries goes
    # negative under a handful of invalid messages → barred from (re)grafting. Without
    # the cap this would be 2_000_000 - 250 > 0 and the peer would keep mesh standing.
    params = ScoreParams()                           # defaults: first_cap=100, w_invalid=-50
    s = PeerScore(first_message_deliveries=1_000_000, invalid_message_deliveries=5)
    assert s.value(params) == 2 * 100 + (-50) * 5    # 200 - 250
    assert s.value(params) < 0


def test_default_first_cap_is_a_nonnegative_int():
    assert type(ScoreParams().first_cap) is int
    assert ScoreParams().first_cap >= 0


def test_negative_first_cap_rejected():
    with pytest.raises(MeshError):
        ScoreParams(first_cap=-1)


def test_score_of_routes_through_the_cap_end_to_end():
    # Integration: drive many genuine first-deliveries through the live mesh and assert
    # the peer's score is bounded by first_cap, not linear in deliveries.
    gs = Gossipsub(
        rng=random.Random(0),
        score_params=ScoreParams(w_time=0, time_cap=0, w_first=2, first_cap=5, w_invalid=-50),
    )
    topic = "web/demo"
    gs.add_peer(topic, "p")
    for k in range(50):
        assert gs.record_delivery("p", topic, f"m{k}") is True
    assert gs._score("p").first_message_deliveries == 50   # raw counter still grows…
    assert gs.score_of("p") == 2 * 5                       # …but the SCORE is capped
