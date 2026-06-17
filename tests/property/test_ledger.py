"""Phase 1 proof: the FBR settlement core is sound.

Property tests over random transfer sequences assert the network's economic
invariants: conservation of value, no double-spend, no overdraft, signature
enforcement. Determinism is seeded so failures reproduce.
"""

import random

import pytest

from knitweb.ledger import loom
from knitweb.ledger.fiber import genesis_fiber
from knitweb.ledger.knit import build, sign_from, sign_to
from knitweb.ledger.node import FBRNode


@pytest.mark.property
def test_single_transfer_moves_value_and_conserves():
    a = FBRNode(genesis_balances={"FBR": 100})
    b = FBRNode()
    sender_before, receiver_before = a.braid.head, b.braid.head
    a.transfer_to(b, "FBR", 30, timestamp=1)
    assert a.balance("FBR") == 70
    assert b.balance("FBR") == 30
    assert loom.conserves_value(
        sender_before, a.braid.head, receiver_before, b.braid.head, "FBR"
    )
    assert a.nonce == 1 and b.nonce == 0  # only the sender consumes a nonce


@pytest.mark.property
def test_random_transfers_conserve_total_supply():
    rng = random.Random(1337)
    n = 6
    nodes = [FBRNode(genesis_balances={"FBR": 1000}) for _ in range(n)]
    total = sum(node.balance("FBR") for node in nodes)
    ts = 0
    for _ in range(400):
        i, j = rng.sample(range(n), 2)
        bal = nodes[i].balance("FBR")
        if bal == 0:
            continue
        amount = rng.randint(1, bal)
        ts += 1
        nodes[i].transfer_to(nodes[j], "FBR", amount, timestamp=ts)
        assert sum(node.balance("FBR") for node in nodes) == total  # invariant each step
    assert all(node.braid.validate() for node in nodes)


@pytest.mark.property
def test_overdraft_is_rejected():
    a = FBRNode(genesis_balances={"FBR": 10})
    b = FBRNode()
    with pytest.raises(ValueError):
        a.transfer_to(b, "FBR", 11, timestamp=1)
    assert a.balance("FBR") == 10 and b.balance("FBR") == 0  # state unchanged


@pytest.mark.property
def test_double_spend_same_knit_is_rejected():
    a = FBRNode(genesis_balances={"FBR": 100})
    b = FBRNode()
    knit = a.propose(b.pub, "FBR", 40, timestamp=1)
    knit = b.accept(knit)
    a.apply_sent(knit)
    # Replaying the exact same knit must fail (nonce already advanced).
    with pytest.raises(loom.LoomError):
        a.apply_sent(knit)


@pytest.mark.property
def test_tampered_amount_breaks_signature():
    a = FBRNode(genesis_balances={"FBR": 100})
    b = FBRNode()
    knit = a.propose(b.pub, "FBR", 10, timestamp=1)
    knit = b.accept(knit)
    forged = knit.__class__(**{**knit.__dict__, "amount": 1000})  # tamper post-signing
    ok, reason = loom.validate_knit(forged)
    assert not ok and "signature" in reason


@pytest.mark.property
def test_unsigned_knit_is_invalid():
    a = FBRNode(genesis_balances={"FBR": 100})
    b = FBRNode()
    knit = build(a.pub, b.pub, "FBR", 5, 0, 1)
    ok, reason = loom.validate_knit(knit)            # no signatures
    assert not ok
    ok, _ = loom.validate_knit(sign_from(knit, a.priv))  # only one signature
    assert not ok


@pytest.mark.property
def test_self_transfer_is_invalid():
    a = FBRNode(genesis_balances={"FBR": 100})
    knit = build(a.pub, a.pub, "FBR", 5, 0, 1)
    knit = sign_to(sign_from(knit, a.priv), a.priv)
    ok, reason = loom.validate_knit(knit)
    assert not ok and "differ" in reason
