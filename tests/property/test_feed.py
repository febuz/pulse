"""Proofs for the signed append-only feed (Phase 3 local core).

These assert the two properties the P2P layer will rely on:
  * a single signed head authenticates the whole feed and any verified slice, and
  * equivocation (two signed histories at the same position) is provable, while a
    legitimate truncate+rewrite (fork bump) is NOT mistaken for an attack.
"""

import random

import pytest

from knitweb.core import crypto
from knitweb.fabric.feed import (
    Feed,
    FeedHead,
    check_conflict,
    check_prefix_conflict,
    verify_entries,
    verify_head,
)


@pytest.mark.property
def test_append_produces_signed_verifiable_head():
    feed = Feed.create()
    head = feed.append({"kind": "knowledge", "v": 1})
    assert head.length == 1 and head.fork == 0
    assert verify_head(head)
    assert head.address == crypto.address(feed.feed)


@pytest.mark.property
def test_one_signature_authenticates_all_entries():
    feed = Feed.create()
    for i in range(20):
        head = feed.append({"i": i, "payload": f"entry-{i}"})
    # A reader with the entries + the single latest head verifies the whole log.
    assert verify_entries(head, feed.entries)
    assert head.length == 20


@pytest.mark.property
def test_root_is_order_sensitive_and_tamper_evident():
    feed = Feed.create()
    feed.append({"i": 0})
    feed.append({"i": 1})
    head = feed.append({"i": 2})
    entries = feed.entries
    # Tamper with one entry -> recomputed root no longer matches the signed head.
    tampered = list(entries)
    tampered[1] = {"i": 99}
    assert not verify_entries(head, tampered)
    # Reorder -> also fails (Merkle root is order-sensitive).
    reordered = [entries[0], entries[2], entries[1]]
    assert not verify_entries(head, reordered)


@pytest.mark.property
def test_wrong_length_claim_is_rejected():
    feed = Feed.create()
    feed.append({"i": 0})
    head = feed.append({"i": 1})
    # Claiming the same head over a different number of entries fails.
    assert not verify_entries(head, feed.entries[:1])


@pytest.mark.property
def test_forged_head_under_another_key_fails():
    feed = Feed.create()
    feed.append({"i": 0})
    good = feed.head()
    # Re-label the head as belonging to a different feed key -> signature invalid.
    _, other_pub = crypto.generate_keypair()
    forged = FeedHead(feed=other_pub, root=good.root, length=good.length,
                      fork=good.fork, sig=good.sig)
    assert not verify_head(forged)


@pytest.mark.property
def test_equivocation_is_provable():
    # The author signs two different entry-3 histories at the same (length, fork).
    priv, _ = crypto.generate_keypair()
    a = Feed(priv)
    b = Feed(priv)
    for f in (a, b):
        f.append({"i": 0})
        f.append({"i": 1})
    head_a = a.append({"i": 2, "side": "A"})
    head_b = b.append({"i": 2, "side": "B"})
    assert head_a.length == head_b.length == 3
    assert head_a.fork == head_b.fork == 0
    assert head_a.root != head_b.root
    assert check_conflict(head_a, head_b)            # caught
    # Two honest heads of the SAME history are not a conflict.
    assert not check_conflict(head_a, head_a)


@pytest.mark.property
def test_legitimate_truncate_rewrite_is_not_equivocation():
    priv, _ = crypto.generate_keypair()
    feed = Feed(priv)
    feed.append({"i": 0})
    feed.append({"i": 1})
    old_head = feed.append({"i": 2})           # length 3, fork 0
    feed.truncate(2)                            # drop entry 2, fork -> 1
    new_head = feed.append({"i": 2, "rev": 2})  # length 3, fork 1, different root
    assert new_head.length == old_head.length   # same position...
    assert new_head.fork != old_head.fork       # ...but a different fork
    assert new_head.root != old_head.root
    assert not check_conflict(old_head, new_head)  # NOT flagged (honest rewrite)


@pytest.mark.property
def test_prefix_rewrite_without_fork_bump_is_caught():
    # Author publishes a 2-entry head, then a 4-entry head at the SAME fork whose
    # first two entries differ from what the short head committed -> tampering.
    priv, _ = crypto.generate_keypair()
    honest = Feed(priv)
    honest.append({"i": 0})
    short_head = honest.append({"i": 1})        # length 2, fork 0

    rewritten = Feed(priv)                       # same key, same fork 0
    rewritten.append({"i": 0})
    rewritten.append({"i": 1, "tampered": True})  # prefix diverges
    rewritten.append({"i": 2})
    long_head = rewritten.append({"i": 3})       # length 4, fork 0

    assert check_prefix_conflict(short_head, long_head, rewritten.entries)
    # An honest extension (same prefix) is NOT flagged.
    cont = Feed(priv)
    cont.append({"i": 0})
    cont.append({"i": 1})
    cont.append({"i": 2})
    honest_long = cont.append({"i": 3})
    assert not check_prefix_conflict(short_head, honest_long, cont.entries)


@pytest.mark.property
def test_random_append_log_always_verifies():
    rng = random.Random(20260617)
    feed = Feed.create()
    head = feed.head()  # empty feed head
    assert verify_entries(head, feed.entries)
    for _ in range(80):
        head = feed.append({"n": rng.randint(0, 10**9), "t": rng.random() > 0.5})
        assert verify_head(head)
    assert verify_entries(head, feed.entries)
    assert head.length == 80
