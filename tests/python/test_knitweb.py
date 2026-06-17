"""
Unit tests for the knitweb Python package.

Covers: addressing, Fiber, Dot, Knot, FBR ledger, KnitweaveGraph, MarketCap.
Run with:  python -m pytest tests/python/test_knitweb.py -v
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

import datetime
import pytest

from knitweb.addressing import addr256, is_valid_addr, addr_distance, ADDR_BITS, ADDR_HEX
from knitweb.fiber import Fiber, FiberRegistry
from knitweb.dot import Dot, DotRegistry, DotType
from knitweb.knot import Knot, KnotRegistry, compute_knot_addr, validate_knot
from knitweb.fbr import (
    FBRLedger, FBR_POSTER_REWARD, FBR_VALIDATOR_REWARD,
    VALIDATORS_REQUIRED, BURN_AFTER_SECONDS, MIN_FBR_TO_VOTE,
)
from knitweb.graph import KnitweaveGraph
from knitweb.market import MarketCap, ADDR_SPACE, MAX_ELEMENTS, MAX_FBR_SUPPLY


# ── Fixtures ─────────────────────────────────────────────────────────────────

POSTER    = "did:key:poster"
SPIDER_A  = "did:key:spiderA"
SPIDER_B  = "did:key:spiderB"
SPIDER_C  = "did:key:spiderC"


def make_knot(line1="Hello knitweb", line2="", author=POSTER) -> Knot:
    return Knot.create(line1=line1, line2=line2, author=author)


def make_ledger():
    return FBRLedger()


def confirmed_knot(ledger: FBRLedger, knot_addr: str, poster: str = POSTER):
    """Drive a knot to confirmation with three unique validators."""
    for spider in [SPIDER_A, SPIDER_B, SPIDER_C]:
        ledger.validate(knot_addr, poster, spider)


# ── 1. Addressing ───────────────────────────────────────────────────────────────

class TestAddressing:
    def test_returns_64_hex_chars(self):
        assert len(addr256("hello")) == ADDR_HEX == 64

    def test_is_valid_addr_true(self):
        assert is_valid_addr(addr256("x"))

    def test_is_valid_addr_false_short(self):
        assert not is_valid_addr("abc")

    def test_is_valid_addr_false_non_hex(self):
        assert not is_valid_addr("z" * 64)

    def test_deterministic(self):
        assert addr256("a", "b") == addr256("a", "b")

    def test_part_separator_prevents_collision(self):
        assert addr256("ab", "c") != addr256("a", "bc")

    def test_different_inputs_differ(self):
        assert addr256("alpha") != addr256("beta")

    def test_bytes_input(self):
        a = addr256(b"raw bytes")
        assert is_valid_addr(a)

    def test_addr_distance_zero_for_equal(self):
        a = addr256("x")
        assert addr_distance(a, a) == 0

    def test_addr_distance_nonzero_for_different(self):
        assert addr_distance(addr256("a"), addr256("b")) != 0

    def test_addr_bits_constant(self):
        assert ADDR_BITS == 256


# ── 2. Fiber ──────────────────────────────────────────────────────────────────

class TestFiber:
    def test_from_did_sets_addr(self):
        f = Fiber.from_did(POSTER)
        assert is_valid_addr(f.addr)

    def test_same_did_same_addr(self):
        assert Fiber.from_did(POSTER).addr == Fiber.from_did(POSTER).addr

    def test_different_did_different_addr(self):
        assert Fiber.from_did(POSTER).addr != Fiber.from_did(SPIDER_A).addr

    def test_silk_default_true(self):
        assert Fiber.from_did(POSTER).silk is True

    def test_label_truncated_to_64(self):
        f = Fiber.from_did(POSTER, label="x" * 100)
        assert len(f.label) == 64

    def test_registry_register(self):
        reg = FiberRegistry()
        f = reg.register(POSTER, label="Alice")
        assert f.did == POSTER
        assert len(reg) == 1

    def test_registry_idempotent(self):
        reg = FiberRegistry()
        reg.register(POSTER)
        reg.register(POSTER)
        assert len(reg) == 1

    def test_registry_get_by_did(self):
        reg = FiberRegistry()
        reg.register(POSTER)
        assert reg.get_by_did(POSTER) is not None

    def test_registry_get_unknown(self):
        assert FiberRegistry().get_by_did("did:unknown") is None

    def test_registry_touch_updates_last_seen(self):
        reg = FiberRegistry()
        f = reg.register(POSTER)
        old = f.last_seen_at
        f.touch()
        assert f.last_seen_at >= old


# ── 3. Dot ───────────────────────────────────────────────────────────────────

class TestDot:
    def test_create_returns_valid_addr(self):
        src = addr256("fiber1")
        dst = addr256("fiber2")
        d = Dot.create(src, dst, DotType.GOSSIP)
        assert is_valid_addr(d.addr)

    def test_undirected_same_addr(self):
        src = addr256("a")
        dst = addr256("b")
        d1 = Dot.create(src, dst, DotType.GOSSIP)
        d2 = Dot.create(dst, src, DotType.GOSSIP)
        assert d1.addr == d2.addr

    def test_different_type_different_addr(self):
        src = addr256("a")
        dst = addr256("b")
        assert Dot.create(src, dst, DotType.GOSSIP).addr != \
               Dot.create(src, dst, DotType.VALIDATES).addr

    def test_registry_add_increments_weight_on_duplicate(self):
        reg = DotRegistry()
        src, dst = addr256("s"), addr256("d")
        d1 = Dot.create(src, dst, DotType.GOSSIP, weight=1.0)
        d2 = Dot.create(src, dst, DotType.GOSSIP, weight=1.0)
        reg.add(d1)
        reg.add(d2)
        assert reg.get(d1.addr).weight == 2.0
        assert len(reg) == 1

    def test_registry_neighbours(self):
        reg = DotRegistry()
        a, b, c = addr256("a"), addr256("b"), addr256("c")
        reg.connect(a, b, DotType.GOSSIP)
        reg.connect(a, c, DotType.VALIDATES)
        assert len(reg.neighbours(a)) == 2

    def test_dot_type_values(self):
        assert DotType.VALIDATES.value == "validates"
        assert DotType.BRIDGES.value   == "bridges"


# ── 4. Knot ───────────────────────────────────────────────────────────────────

class TestKnot:
    def test_addr_is_256bit(self):
        k = make_knot()
        assert is_valid_addr(k.addr)

    def test_deterministic_addr(self):
        k1 = Knot.create("hello", "world", POSTER, ts="2026-01-01T00:00:00Z")
        k2 = Knot.create("hello", "world", POSTER, ts="2026-01-01T00:00:00Z")
        assert k1.addr == k2.addr

    def test_different_line1_different_addr(self):
        k1 = Knot.create("hello", ts="2026-01-01T00:00:00Z")
        k2 = Knot.create("world", ts="2026-01-01T00:00:00Z")
        assert k1.addr != k2.addr

    def test_validate_ok(self):
        k = make_knot()
        assert validate_knot(k) == {"ok": True}

    def test_validate_blank_line1(self):
        k = make_knot(line1="   ")
        k.line1 = "   "
        result = validate_knot(k)
        assert not result["ok"]

    def test_validate_line1_too_long(self):
        k = Knot.create("x" * 141)
        result = validate_knot(k)
        assert not result["ok"]
        assert "exceeds" in result["reason"]

    def test_validate_tampered_addr(self):
        k = make_knot()
        k.addr = "0" * 64
        result = validate_knot(k)
        assert not result["ok"]
        assert "mismatch" in result["reason"]

    def test_registry_add_and_get(self):
        reg = KnotRegistry()
        k = make_knot()
        assert reg.add(k) == {"ok": True}
        assert reg.get(k.addr) is k

    def test_registry_idempotent(self):
        reg = KnotRegistry()
        k = make_knot()
        reg.add(k)
        reg.add(k)
        assert len(reg) == 1

    def test_registry_evicts_lru(self):
        reg = KnotRegistry(max_knots=2)
        k1 = Knot.create("post 1", ts="2026-01-01T00:00:00.000Z")
        k2 = Knot.create("post 2", ts="2026-01-02T00:00:00.000Z")
        k3 = Knot.create("post 3", ts="2026-01-03T00:00:00.000Z")
        reg.add(k1); reg.add(k2); reg.add(k3)
        assert len(reg) == 2
        assert reg.get(k1.addr) is None

    def test_registry_list_newest_first(self):
        reg = KnotRegistry()
        k1 = Knot.create("first",  ts="2026-01-01T00:00:00.000Z")
        k2 = Knot.create("second", ts="2026-01-02T00:00:00.000Z")
        reg.add(k1); reg.add(k2)
        listed = reg.list(10, 0)
        assert listed[0].addr == k2.addr


# ── 5. FBR Ledger ───────────────────────────────────────────────────────────────

class TestFBRLedger:
    def test_wallet_created_on_access(self):
        ledger = make_ledger()
        w = ledger.wallet(POSTER)
        assert w.did == POSTER
        assert w.balance == 0

    def test_earn_increases_balance(self):
        ledger = make_ledger()
        ledger.wallet(POSTER).earn(10)
        assert ledger.wallet(POSTER).balance == 10

    def test_voting_eligible_zero_balance(self):
        assert not FBRLedger().is_voting_eligible(POSTER)

    def test_voting_eligible_after_earn(self):
        ledger = make_ledger()
        ledger.wallet(POSTER).earn(MIN_FBR_TO_VOTE)
        assert ledger.is_voting_eligible(POSTER)

    def test_self_validation_rejected(self):
        ledger = make_ledger()
        ok, reason = ledger.validate("k" * 64, POSTER, POSTER)
        assert not ok
        assert "own" in reason

    def test_duplicate_validation_rejected(self):
        ledger = make_ledger()
        addr = "k" * 64
        ledger.validate(addr, POSTER, SPIDER_A)
        ok, reason = ledger.validate(addr, POSTER, SPIDER_A)
        assert not ok
        assert "already" in reason

    def test_third_validation_confirms_and_mints(self):
        ledger = make_ledger()
        addr = "k" * 64
        ledger.validate(addr, POSTER, SPIDER_A)
        ledger.validate(addr, POSTER, SPIDER_B)
        ok, event = ledger.validate(addr, POSTER, SPIDER_C)
        assert ok
        assert event == "confirmed"
        assert ledger.wallet(POSTER).balance   == FBR_POSTER_REWARD
        assert ledger.wallet(SPIDER_A).balance == FBR_VALIDATOR_REWARD

    def test_poster_becomes_eligible_after_confirmation(self):
        ledger = make_ledger()
        addr = "k" * 64
        confirmed_knot(ledger, addr)
        assert ledger.is_voting_eligible(POSTER)

    def test_validators_become_eligible_after_confirmation(self):
        ledger = make_ledger()
        addr = "k" * 64
        confirmed_knot(ledger, addr)
        for s in [SPIDER_A, SPIDER_B, SPIDER_C]:
            assert ledger.is_voting_eligible(s)

    def test_post_confirmation_further_votes_rejected(self):
        ledger = make_ledger()
        addr = "k" * 64
        confirmed_knot(ledger, addr)
        ok, reason = ledger.validate(addr, POSTER, "did:key:d")
        assert not ok

    def test_run_burn_zeros_inactive_wallets(self):
        ledger = make_ledger()
        w = ledger.wallet(POSTER)
        w.earn(100)
        old = (datetime.datetime.now(datetime.timezone.utc) -
               datetime.timedelta(seconds=BURN_AFTER_SECONDS + 1)).isoformat()
        w.last_activity_at = old
        result = ledger.run_burn()
        assert result["wallets_affected"] == 1
        assert result["fbr_burned"] == 100
        assert w.balance == 0
        assert w.burned_total == 100

    def test_run_burn_skips_active_wallets(self):
        ledger = make_ledger()
        ledger.wallet(POSTER).earn(50)
        result = ledger.run_burn()
        assert result["wallets_affected"] == 0
        assert ledger.wallet(POSTER).balance == 50

    def test_run_burn_skips_zero_balance(self):
        ledger = make_ledger()
        w = ledger.wallet(POSTER)
        old = (datetime.datetime.now(datetime.timezone.utc) -
               datetime.timedelta(seconds=BURN_AFTER_SECONDS + 1)).isoformat()
        w.last_activity_at = old
        result = ledger.run_burn()
        assert result["wallets_affected"] == 0

    def test_validation_status_before_any_validation(self):
        ledger = make_ledger()
        s = ledger.validation_status("k" * 64)
        assert s["validations"] == 0
        assert not s["confirmed"]
        assert s["needed"] == VALIDATORS_REQUIRED

    def test_validation_status_after_one(self):
        ledger = make_ledger()
        addr = "k" * 64
        ledger.validate(addr, POSTER, SPIDER_A)
        s = ledger.validation_status(addr)
        assert s["validations"] == 1
        assert s["needed"] == VALIDATORS_REQUIRED - 1

    def test_two_knots_independent(self):
        ledger = make_ledger()
        a1, a2 = "a" * 64, "b" * 64
        confirmed_knot(ledger, a1, POSTER)
        confirmed_knot(ledger, a2, "did:key:poster2")
        assert ledger.wallet(POSTER).balance == FBR_POSTER_REWARD
        assert ledger.wallet("did:key:poster2").balance == FBR_POSTER_REWARD
        assert ledger.wallet(SPIDER_A).balance == FBR_VALIDATOR_REWARD * 2

    def test_stats_structure(self):
        s = make_ledger().stats()
        assert s["token"] == "FBR"
        assert "circulating_micro_fbr" in s
        assert "burn_after_days" in s


# ── 6. KnitweaveGraph ───────────────────────────────────────────────────────────────

class TestKnitweaveGraph:
    def test_post_knot_returns_addr(self):
        g = KnitweaveGraph()
        result = g.post_knot("hello knitweb")
        assert result["ok"]
        assert is_valid_addr(result["addr"])

    def test_post_knot_registers_fiber(self):
        g = KnitweaveGraph()
        g.post_knot("test", author=POSTER)
        assert g.fibers.get_by_did(POSTER) is not None

    def test_validate_knot_unknown_returns_error(self):
        g = KnitweaveGraph()
        r = g.validate_knot("0" * 64, SPIDER_A)
        assert not r["ok"]
        assert "not found" in r["reason"]

    def test_validate_knot_full_flow(self):
        g = KnitweaveGraph()
        r = g.post_knot("mint me", author=POSTER)
        addr = r["addr"]
        g.validate_knot(addr, SPIDER_A)
        g.validate_knot(addr, SPIDER_B)
        r3 = g.validate_knot(addr, SPIDER_C)
        assert r3["ok"]
        assert r3["event"] == "confirmed"
        assert g.ledger.wallet(POSTER).balance == FBR_POSTER_REWARD

    def test_validate_adds_dot(self):
        g = KnitweaveGraph()
        r = g.post_knot("dot test", author=POSTER)
        addr = r["addr"]
        g.validate_knot(addr, SPIDER_A)
        assert len(g.dots) == 1

    def test_stats_structure(self):
        g = KnitweaveGraph()
        s = g.stats()
        assert "graph" in s
        assert "fbr" in s
        assert "market" in s

    def test_list_knots_newest_first(self):
        g = KnitweaveGraph()
        g.post_knot("first",  author=POSTER, ts="2026-01-01T00:00:00Z")
        g.post_knot("second", author=POSTER, ts="2026-01-02T00:00:00Z")
        knots = g.list_knots(10, 0)
        assert knots[0].line1 == "second"


# ── 7. MarketCap ─────────────────────────────────────────────────────────────────

class TestMarketCap:
    def test_addr_bits_256(self):
        assert MarketCap().addr_bits == 256

    def test_three_dimensions(self):
        assert MarketCap().dimensions == 3

    def test_fiber_space_equals_2_pow_256(self):
        assert MarketCap().fiber_space == 2 ** 256

    def test_total_elements_three_times_space(self):
        mc = MarketCap()
        assert mc.total_elements == 3 * mc.fiber_space

    def test_max_fbr_supply_bounded(self):
        assert MAX_FBR_SUPPLY > 0
        assert MAX_FBR_SUPPLY == ADDR_SPACE * (FBR_POSTER_REWARD + VALIDATORS_REQUIRED * FBR_VALIDATOR_REWARD)

    def test_summary_keys(self):
        s = MarketCap().summary()
        assert "dimensions" in s
        assert "capacity" in s
        assert "fbr_token" in s

    def test_utilisation_returns_tuples(self):
        u = MarketCap().utilisation(100, 200, 300)
        assert u["fiber_utilisation"] == (100, ADDR_SPACE)
        assert u["combined_elements"] == 600

    def test_market_cap_constants_consistent(self):
        assert MAX_ELEMENTS == 3 * ADDR_SPACE
