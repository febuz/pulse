"""Integration proofs: detected/proven misbehavior → reputation consequence (#56 + #57).

A verified equivocation report or a genuine feed conflict must penalize the offending key in the
reputation ledger; unverifiable evidence or a non-conflict must be a no-op (no penalty on hearsay).
"""

from knitweb.core import crypto
from knitweb.fabric.equivocation import prove_equivocation
from knitweb.fabric.feed import Feed, FeedHead
from knitweb.p2p.policing import (
    police_equivocation_report,
    police_feed_conflict,
    police_invalid_proof,
)
from knitweb.p2p.reputation import Offense, PeerReputation

REPORTER = "did:key:watcher"


def _signed_head(priv, feed, root, length, fork):
    tmp = FeedHead(feed=feed, root=root, length=length, fork=fork, sig="")
    return FeedHead(feed=feed, root=root, length=length, fork=fork, sig=crypto.sign(priv, tmp.signable()))


def _equivocating_pair():
    priv, feed = crypto.generate_keypair()
    a = _signed_head(priv, feed, "aa" * 32, 3, 0)
    b = _signed_head(priv, feed, "bb" * 32, 3, 0)
    return feed, a, b


# ── 1. Proven equivocation → instant ban ─────────────────────────────────────

def test_verified_equivocation_report_bans_offender():
    feed, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    rep = PeerReputation()
    assert police_equivocation_report(rep, report) is True
    assert rep.is_banned(feed)                       # EQUIVOCATION is full-threshold
    assert rep.score(feed) == Offense.EQUIVOCATION.value


def test_unverifiable_report_is_a_noop():
    feed, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    # tamper: point the report at a different feed key → no longer verifies
    _, other = crypto.generate_keypair()
    from dataclasses import replace
    bad = replace(report, feed=other)
    rep = PeerReputation()
    assert police_equivocation_report(rep, bad) is False
    assert rep.score(other) == 0 and not rep.is_banned(other)   # no penalty on bad evidence


# ── 2. Feed conflict → penalty ───────────────────────────────────────────────

def test_feed_conflict_penalizes_offender():
    feed, a, b = _equivocating_pair()
    rep = PeerReputation()
    assert police_feed_conflict(rep, a, b) is True
    assert rep.is_banned(feed)


def test_non_conflict_is_a_noop():
    f = Feed.create()
    f.append({"i": 0})
    h1 = f.head()
    f.append({"i": 1})
    h2 = f.head()                                    # different length — honest growth
    rep = PeerReputation()
    assert police_feed_conflict(rep, h1, h2) is False
    assert rep.tracked() == 0                        # nobody penalized


def test_fork_bumped_rewrite_is_not_policed():
    f = Feed.create()
    f.append({"i": 0}); f.append({"i": 1})
    before = f.head()
    f.truncate(1); f.append({"i": 9})
    after = f.head()                                 # bumped fork — legitimate
    rep = PeerReputation()
    assert police_feed_conflict(rep, before, after) is False
    assert not rep.is_banned(before.feed)


# ── 3. Invalid proof penalty + accumulation ──────────────────────────────────

def test_invalid_proof_penalizes_and_accumulates_to_ban():
    rep = PeerReputation()
    peer = "did:key:badseeder"
    # one stale proof (50) is not yet a ban…
    assert police_invalid_proof(rep, peer) is False
    assert rep.score(peer) == Offense.STALE_OR_FORGED_PROOF.value
    # …a second crosses the 100 threshold.
    assert police_invalid_proof(rep, peer) is True
    assert rep.is_banned(peer)


def test_policing_shares_one_ledger_across_offenses():
    # a peer that serves a bad proof (50) and is then caught equivocating (100) is banned,
    # and the ledger reflects both events on the same key.
    feed, a, b = _equivocating_pair()
    rep = PeerReputation()
    police_invalid_proof(rep, feed)                  # 50
    police_feed_conflict(rep, a, b)                  # +100 → 150
    assert rep.score(feed) == 150 and rep.is_banned(feed)
