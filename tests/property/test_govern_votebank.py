"""Proofs for governance: demographic vote supply, the VoteBank, and recency weighting.

Principles under test:
  * No premine — a fresh bank holds the whole supply in treasury, issued nothing.
  * One vote per person, worldwide, across national AND freeport registration paths.
  * Max supply = registered persons (national + freeport) + this year's expected births,
    counted per world (e.g. the moon).
  * The bank never issues past the demographic cap.
  * When agents vote, more recent votes weigh exponentially more.
"""

import pytest

from knitweb.govern import (
    Decay,
    Registration,
    RegistrationKind,
    Vote,
    VoteBank,
    WorldRegistry,
    register_freeport,
    register_national,
    tally,
)


# --- registry / demographic supply -------------------------------------------------------


@pytest.mark.property
def test_national_and_freeport_both_count_toward_supply():
    reg = WorldRegistry(year=2026)
    assert reg.register(register_national("earth", "NL-12345", timestamp=1))
    assert reg.register(
        register_freeport("earth", imei="3570:99", email="a@freeport", ad_hoc_proof="selfie+vow", timestamp=2)
    )
    assert reg.registered_persons() == 2
    # Freeport registrations are included in the cap (no expected births set yet).
    assert reg.max_vote_supply() == 2


@pytest.mark.property
def test_one_vote_per_person_dedup_across_worlds():
    reg = WorldRegistry(year=2026)
    first = register_national("earth", "NL-12345", timestamp=1)
    again = register_national("moon", "NL-12345", timestamp=5)  # same human, different world
    assert reg.register(first) is True
    assert reg.register(again) is False                          # rejected: already registered
    assert reg.registered_persons() == 1
    assert reg.world_of(first.subject) == "earth"


@pytest.mark.property
def test_freeport_dedup_on_imei_email_pair():
    reg = WorldRegistry(year=2026)
    a = register_freeport("earth", imei="IMEI-1", email="x@fp", ad_hoc_proof="p1", timestamp=1)
    b = register_freeport("earth", imei="IMEI-1", email="x@fp", ad_hoc_proof="p2", timestamp=2)
    assert a.subject == b.subject                                # same person ⇒ same dedup key
    assert reg.register(a) and not reg.register(b)
    assert reg.registered_persons() == 1


@pytest.mark.property
def test_moon_supply_is_persons_plus_expected_births():
    # "1,000,000 registered inhabitants on the moon ⇒ 1 vote per person plus the year's births."
    reg = WorldRegistry(year=2026)
    for i in range(1000):  # stand-in for a larger registry; the rule is what matters
        reg.register(register_national("moon", f"MOON-{i}", timestamp=i))
    reg.set_expected_births("moon", 37)
    assert reg.registered_persons("moon") == 1000
    assert reg.expected_births("moon") == 37
    assert reg.max_vote_supply("moon") == 1037                   # persons + birth allowance
    assert reg.max_vote_supply() == 1037                         # only world present


@pytest.mark.property
def test_supply_sums_across_worlds():
    reg = WorldRegistry(year=2026)
    reg.register(register_national("earth", "E-1", timestamp=1))
    reg.register(register_national("moon", "M-1", timestamp=2))
    reg.set_expected_births("earth", 3)
    reg.set_expected_births("moon", 1)
    assert reg.max_vote_supply() == (2 + 4)                      # 2 persons + 4 births
    assert sorted(reg.worlds()) == ["earth", "moon"]


@pytest.mark.property
def test_registration_is_content_addressed_and_pii_free():
    r = register_national("earth", "secret-national-id", timestamp=1)
    rec = r.to_record()
    # No raw PII anywhere in the canonical record — only digests.
    assert "secret-national-id" not in repr(rec)
    assert r.cid.startswith("b") and r.subject != r.proof


@pytest.mark.property
def test_bad_registration_inputs_rejected():
    with pytest.raises(TypeError):
        register_national("earth", "", timestamp=1)
    with pytest.raises(TypeError):
        register_freeport("earth", imei="x", email="y", ad_hoc_proof="", timestamp=1)
    with pytest.raises(TypeError):
        WorldRegistry(year=2026).set_expected_births("earth", True)  # bool is not int


# --- votebank issuance --------------------------------------------------------------------


@pytest.mark.property
def test_no_premine_bank_holds_whole_supply():
    reg = WorldRegistry(year=2026)
    reg.register(register_national("earth", "E-1", timestamp=1))
    reg.set_expected_births("earth", 4)
    bank = VoteBank(reg)
    assert bank.issued == 0 and bank.issuances == []
    assert bank.treasury_remaining() == 5                        # 1 person + 4 births, all in bank


@pytest.mark.property
def test_issue_one_vote_per_person():
    reg = WorldRegistry(year=2026)
    r = register_national("earth", "E-1", timestamp=1)
    reg.register(r)
    bank = VoteBank(reg)
    first = bank.issue(r, beat=10)
    assert first is not None and first.subject == r.subject
    assert bank.issued == 1 and bank.treasury_remaining() == 0
    assert bank.issue(r, beat=11) is None                        # no double-issue to same person


@pytest.mark.property
def test_cannot_issue_to_unregistered_person():
    reg = WorldRegistry(year=2026)
    bank = VoteBank(reg)
    stranger = register_national("earth", "NOBODY", timestamp=1)
    with pytest.raises(ValueError, match="not registered"):
        bank.issue(stranger, beat=1)


@pytest.mark.property
def test_issuance_never_exceeds_demographic_cap():
    # Two people, zero expected births ⇒ cap 2. A third (somehow) cannot over-issue.
    reg = WorldRegistry(year=2026)
    people = [register_national("earth", f"E-{i}", timestamp=i) for i in range(2)]
    for p in people:
        reg.register(p)
    bank = VoteBank(reg)
    assert bank.issue(people[0], beat=1) is not None
    assert bank.issue(people[1], beat=1) is not None
    assert bank.issued == 2 and bank.treasury_remaining() == 0
    # Register a third only AFTER the bank cap was measured against 2 — simulate exhaustion by
    # checking the guard directly: a fresh person beyond the (frozen) supply gets nothing.
    extra = register_national("earth", "E-extra", timestamp=9)
    reg2 = WorldRegistry(year=2026)
    reg2.register(extra)
    bank2 = VoteBank(reg2)
    bank2.issued = bank2.registry.max_vote_supply()             # pretend supply already drawn
    assert bank2.issue(extra, beat=1) is None


@pytest.mark.property
def test_issuance_is_auditable():
    reg = WorldRegistry(year=2026)
    r = register_freeport("moon", imei="I-9", email="m@fp", ad_hoc_proof="vow", timestamp=1)
    reg.register(r)
    bank = VoteBank(reg)
    iss = bank.issue(r, beat=42)
    assert iss.world == "moon" and iss.beat == 42 and iss.supply_at_issue == 1
    assert iss.cid.startswith("b")


# --- recency-weighted tally ---------------------------------------------------------------


@pytest.mark.property
def test_recent_votes_weigh_exponentially_more():
    # Same number of votes each side, but YES votes are fresher than NO votes ⇒ YES wins.
    decay = Decay(num=1, den=2)                                  # halve weight each beat
    votes = [
        Vote(choice="yes", subject="s1", beat=10),              # age 0
        Vote(choice="yes", subject="s2", beat=10),              # age 0
        Vote(choice="no", subject="s3", beat=5),                # age 5
        Vote(choice="no", subject="s4", beat=5),                # age 5
    ]
    result = tally(votes, now=10, decay=decay)
    assert result.winner == "yes"
    assert result.weights["yes"] > result.weights["no"]
    # Exponential: an age-5 vote is worth 1/32 of an age-0 vote at half-life-per-beat.
    assert result.weights["yes"] == 2 * (1 << 20)
    assert result.weights["no"] == 2 * ((1 << 20) >> 5)


@pytest.mark.property
def test_weight_decays_geometrically():
    d = Decay(num=1, den=2, scale=1024)
    assert d.weight(0) == 1024
    assert d.weight(1) == 512
    assert d.weight(2) == 256
    assert d.weight(10) == 1                                     # 1024 / 2^10
    assert d.weight(11) == 0                                     # floored out


@pytest.mark.property
def test_horizon_drops_stale_votes():
    d = Decay(num=9, den=10, scale=1_000_000, horizon=3)
    assert d.weight(3) > 0
    assert d.weight(4) == 0                                      # beyond horizon ⇒ no weight


@pytest.mark.property
def test_one_vote_per_subject_enforced_in_tally():
    votes = [Vote("yes", "s1", 1), Vote("no", "s1", 2)]         # same person twice
    with pytest.raises(ValueError, match="more than once"):
        tally(votes, now=2)


@pytest.mark.property
def test_future_vote_rejected():
    with pytest.raises(ValueError, match="future"):
        tally([Vote("yes", "s1", 5)], now=3)


@pytest.mark.property
def test_tie_breaks_deterministically_and_margin():
    # Equal, simultaneous weight on two choices ⇒ deterministic smallest-choice winner.
    votes = [Vote("bbb", "s1", 0), Vote("aaa", "s2", 0)]
    result = tally(votes, now=0, decay=Decay(scale=100))
    assert result.winner == "aaa" and result.margin() == 0
    assert result.weights == {"bbb": 100, "aaa": 100}


@pytest.mark.property
def test_decay_rejects_non_shrinking_and_bool():
    with pytest.raises(ValueError, match="num < den"):
        Decay(num=2, den=2)
    with pytest.raises(TypeError):
        Decay(num=True)


@pytest.mark.property
def test_end_to_end_register_issue_vote():
    # Full loop: register people (both paths), draw their votes from the bank, tally with recency.
    reg = WorldRegistry(year=2026)
    alice = register_national("earth", "NL-A", timestamp=1)
    bob = register_freeport("earth", imei="I-B", email="b@fp", ad_hoc_proof="vow", timestamp=2)
    for p in (alice, bob):
        reg.register(p)
    reg.set_expected_births("earth", 1)
    bank = VoteBank(reg)
    assert bank.treasury_remaining() == 3                        # 2 persons + 1 birth allowance

    a_iss = bank.issue(alice, beat=20)
    b_iss = bank.issue(bob, beat=24)
    assert a_iss is not None and b_iss is not None
    assert bank.issued == 2 and bank.treasury_remaining() == 1   # the unborn's vote stays in bank

    # Alice (earlier) votes no, Bob (later) votes yes ⇒ Bob's fresher vote dominates.
    votes = [Vote("no", alice.subject, a_iss.beat), Vote("yes", bob.subject, b_iss.beat)]
    result = tally(votes, now=24, decay=Decay(num=1, den=2))
    assert result.winner == "yes" and result.n == 2
