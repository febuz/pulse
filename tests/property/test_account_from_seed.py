"""AccountNode.from_seed — deterministic account from an external id (app bridging)."""
from knitweb.ledger.node import AccountNode


def test_from_seed_is_deterministic():
    a, b = AccountNode.from_seed("wallet:42"), AccountNode.from_seed("wallet:42")
    assert a.address == b.address and a.pub == b.pub and a.priv == b.priv


def test_distinct_seeds_distinct_accounts():
    assert AccountNode.from_seed("alice").address != AccountNode.from_seed("bob").address


def test_from_seed_account_settles_real_knits():
    a = AccountNode.from_seed("alice", {"PLS": 100})
    b = AccountNode.from_seed("bob")
    assert a.balance("PLS") == 100
    a.transfer_to(b, "PLS", 10, timestamp=1)
    assert a.balance("PLS") == 90 and b.balance("PLS") == 10
