"""Proofs for equivocation fraud reports.

Equivocation — one feed key signing two different roots at the same (length, fork) — must be
packageable as a canonical record that any third party can verify from the bytes alone, while an
honest author (including a legitimate fork-bumped rewrite) can never be falsely reported.
"""

from dataclasses import replace


from knitweb.core import canonical, crypto
from knitweb.fabric.equivocation import (
    EquivocationReport,
    prove_equivocation,
    verify_equivocation_report,
)
from knitweb.fabric.feed import Feed, FeedHead

REPORTER = "did:key:watcher"


def _signed_head(priv, feed, root, length, fork):
    """Sign an arbitrary head with ``priv`` (lets us forge an equivocation in a test)."""
    tmp = FeedHead(feed=feed, root=root, length=length, fork=fork, sig="")
    sig = crypto.sign(priv, tmp.signable())
    return FeedHead(feed=feed, root=root, length=length, fork=fork, sig=sig)


def _equivocating_pair():
    """Two validly-signed heads from one key at the same (length, fork), different roots."""
    priv, feed = crypto.generate_keypair()
    a = _signed_head(priv, feed, root="aa" * 32, length=3, fork=0)
    b = _signed_head(priv, feed, root="bb" * 32, length=3, fork=0)
    return priv, feed, a, b


# ── 1. A genuine equivocation produces a verifiable report ───────────────────

def test_equivocation_is_proven_and_verifies():
    _, feed, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    assert report is not None
    assert report.offender == feed
    assert verify_equivocation_report(report)


def test_report_round_trips_through_canonical_record():
    _, _, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    rec = report.to_record()
    assert rec["kind"] == "equivocation-report"
    # rebuild from the record and re-verify (a relayed report is checkable on its own)
    rebuilt = EquivocationReport(
        feed=rec["feed"], head_a=rec["head_a"], head_b=rec["head_b"], reporter=rec["reporter"]
    )
    assert verify_equivocation_report(rebuilt)
    assert canonical.cid(rec)  # content-addressable


def test_report_is_canonical_regardless_of_head_order():
    _, _, a, b = _equivocating_pair()
    r1 = prove_equivocation(a, b, REPORTER)
    r2 = prove_equivocation(b, a, REPORTER)        # swapped argument order
    assert r1.cid == r2.cid                          # canonical (sorted by root)


# ── 2. Honest authors are never reportable ───────────────────────────────────

def test_two_heads_at_different_lengths_is_not_equivocation():
    f = Feed.create()
    f.append({"i": 0})
    head1 = f.head()
    f.append({"i": 1})
    head2 = f.head()                                 # different length — honest growth
    assert prove_equivocation(head1, head2, REPORTER) is None


def test_legitimate_fork_bump_rewrite_is_not_equivocation():
    f = Feed.create()
    f.append({"i": 0})
    f.append({"i": 1})
    before = f.head()
    f.truncate(1)                                    # honest rewrite — bumps fork
    f.append({"i": 99})
    after = f.head()
    # same author, overlapping length, but different fork ⇒ NOT a conflict
    assert prove_equivocation(before, after, REPORTER) is None


def test_same_head_twice_is_not_equivocation():
    f = Feed.create()
    f.append({"i": 0})
    h = f.head()
    assert prove_equivocation(h, h, REPORTER) is None


def test_different_feeds_is_not_equivocation():
    pa, fa = crypto.generate_keypair()
    pb, fb = crypto.generate_keypair()
    a = _signed_head(pa, fa, "aa" * 32, 1, 0)
    b = _signed_head(pb, fb, "bb" * 32, 1, 0)
    assert prove_equivocation(a, b, REPORTER) is None


# ── 3. Forged / tampered reports are rejected ────────────────────────────────

def test_unsigned_head_is_not_provable():
    priv, feed = crypto.generate_keypair()
    good = _signed_head(priv, feed, "aa" * 32, 2, 0)
    forged = FeedHead(feed=feed, root="bb" * 32, length=2, fork=0, sig="00" * 70)  # bad sig
    assert prove_equivocation(good, forged, REPORTER) is None


def test_tampered_report_fails_verification():
    _, _, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    # swap one head's root to a value its signature does not cover ⇒ signature no longer valid
    bad_head = {**report.head_a, "root": "cc" * 32}
    tampered = replace(report, head_a=bad_head)
    assert not verify_equivocation_report(tampered)


def test_report_pointing_at_wrong_feed_fails():
    _, _, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    _, other_feed = crypto.generate_keypair()
    assert not verify_equivocation_report(replace(report, feed=other_feed))


def test_malformed_report_missing_fields_fails():
    _, _, a, b = _equivocating_pair()
    report = prove_equivocation(a, b, REPORTER)
    assert not verify_equivocation_report(replace(report, head_a={"root": "aa" * 32}))
