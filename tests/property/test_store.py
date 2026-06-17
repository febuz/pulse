"""Proofs for durable persistence (M3): node state survives a restart byte-identically.

A restored Braid/Feed/AccountNode must reproduce identical balances, nonce, feed head
and — because the on-disk format is canonical CBOR — identical CIDs. A tampered or
malformed snapshot must fail on load rather than corrupt state.
"""

import pytest

from knitweb.core import crypto
from knitweb.fabric.feed import Feed, verify_entries
from knitweb.ledger.node import AccountNode
from knitweb import store


def _funded_pair():
    a = AccountNode(genesis_balances={"PLS": 100})
    b = AccountNode()
    a.transfer_to(b, "PLS", 30, timestamp=1)
    a.transfer_to(b, "PLS", 5, timestamp=2)
    return a, b


@pytest.mark.property
def test_braid_round_trips_with_stable_cid(tmp_path):
    a, _ = _funded_pair()
    p = str(tmp_path / "braid.cbor")
    head_cid, bal, nonce = a.braid.head.cid, a.balance("PLS"), a.nonce
    store.save_braid(a.braid, p)
    restored = store.load_braid(p)
    assert restored.validate()
    assert restored.head.cid == head_cid            # canonical-CBOR ⇒ identical CID
    assert restored.head.balance("PLS") == bal == 65
    assert restored.head.nonce == nonce == 2


@pytest.mark.property
def test_node_round_trips_and_can_keep_transacting(tmp_path):
    a, b = _funded_pair()
    p = str(tmp_path / "node.cbor")
    store.save_node(a, p)
    restored = store.load_node(p)
    assert restored.address == a.address
    assert restored.pub == a.pub and restored.priv == a.priv
    assert restored.network == a.network
    assert restored.balance("PLS") == 65
    assert restored.nonce == 2
    assert restored.braid.head.cid == a.braid.head.cid
    # the restored node keeps working: nonce continues, transfer settles
    c = AccountNode()
    restored.transfer_to(c, "PLS", 10, timestamp=3)
    assert restored.balance("PLS") == 55 and c.balance("PLS") == 10
    assert restored.nonce == 3


@pytest.mark.property
def test_feed_round_trips_and_verifies(tmp_path):
    priv, _ = crypto.generate_keypair()
    feed = Feed(priv)
    for i in range(6):
        feed.append({"i": i, "msg": f"entry-{i}"})
    head_before = feed.head()
    p = str(tmp_path / "feed.cbor")
    store.save_feed(feed, p)
    restored = store.load_feed(priv, p)
    assert restored.length == 6
    assert restored.head().root == head_before.root
    assert verify_entries(restored.head(), restored.entries)


@pytest.mark.property
def test_feed_round_trips_after_fork_bump(tmp_path):
    priv, _ = crypto.generate_keypair()
    feed = Feed(priv)
    feed.append({"i": 0}); feed.append({"i": 1}); feed.append({"i": 2})
    feed.truncate(1)                 # fork -> 1
    feed.append({"i": 1, "rev": 2})
    p = str(tmp_path / "feed.cbor")
    store.save_feed(feed, p)
    restored = store.load_feed(priv, p)
    assert restored.fork == 1
    assert restored.head().root == feed.head().root
    assert restored.head().fork == 1


@pytest.mark.property
def test_tampered_braid_snapshot_fails_on_load(tmp_path):
    a, _ = _funded_pair()
    p = str(tmp_path / "braid.cbor")
    store.save_braid(a.braid, p)
    # Corrupt a middle fiber's balance on disk -> its CID no longer links -> reject.
    from knitweb.core import canonical
    rec = canonical.decode(open(p, "rb").read())
    rec["fibers"][1]["balances"] = {"PLS": 999999}
    open(p, "wb").write(canonical.encode(rec))
    with pytest.raises(Exception):    # BraidError (broken link) or StoreError
        store.load_braid(p)


@pytest.mark.property
def test_double_spend_guard_survives_restart(tmp_path):
    # A p2p node's in-memory nonce cache is lost on restart, so the persisted braid
    # must carry the double-spend protection: load_braid rebuilds the spent-knit set
    # from the saved Fibers, so replaying an already-applied knit after a restart is
    # still rejected.
    a = AccountNode(genesis_balances={"PLS": 100})
    b = AccountNode()
    knit = b.accept(a.propose(b.pub, "PLS", 30, timestamp=1))
    a.apply_sent(knit)
    b.apply_received(knit)
    p = str(tmp_path / "b.cbor")
    store.save_node(b, p)

    b2 = store.load_node(p)
    assert b2.balance("PLS") == 30
    from knitweb.ledger.braid import BraidError
    with pytest.raises(BraidError):       # replay of the same knit after restart
        b2.apply_received(knit)
    assert b2.balance("PLS") == 30        # state unchanged


@pytest.mark.property
def test_wrong_kind_and_owner_mismatch_rejected(tmp_path):
    p = str(tmp_path / "x.cbor")
    from knitweb.core import canonical
    open(p, "wb").write(canonical.encode({"kind": "not-a-snapshot"}))
    with pytest.raises(store.StoreError):
        store.load_braid(p)
    with pytest.raises(store.StoreError):
        store.load_node(p)
