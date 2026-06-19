"""End-to-end proof for the Spider PoUW compute marketplace (#14).

One bounded job runs advertise → schedule → execute → verify → reward, composed from
the shipped PoUW primitives (scheduler/challenge/sampling/committee/quorum/dispute) and
the demand-gated, no-premine PLS mint (``token.mint``). The invariants under test:

  (a) a CORRECT result lifts the spider's PLS by exactly the bounded integer reward;
  (b) a TAMPERED/WRONG result is caught by the committee, slashed, and earns NOTHING;
  (c) PLS is conserved / demand-gated — no premine, and total supply grows only by the
      bounded reward (≤ escrow), never more.
"""

import pytest

from knitweb.ledger.node import AccountNode
from knitweb.pouw.marketplace import ComputeJob, Marketplace, SpiderAd
from knitweb.token.mint import NATIVE, EmissionPolicy, Treasury

PRICE = 1
N_BLOCKS = 16
GENESIS = 1000          # client's starting PLS (test seeding; no native premine)
SUBMIT_BEAT = 100
VERIFIERS = [f"did:key:verifier-{i}" for i in range(9)]


def _setup(rate_num=1, rate_den=2):
    mkt = Marketplace(treasury=Treasury(EmissionPolicy(rate_num=rate_num, rate_den=rate_den)))
    spider = AccountNode()
    ad = SpiderAd(spider=spider.address, gpus=2, ram_mib=2048, price_per_block=PRICE)
    mkt.advertise(ad, VERIFIERS)
    client = AccountNode(genesis_balances={NATIVE: GENESIS})
    return mkt, ad, client, spider


@pytest.mark.property
def test_no_premine_before_any_work():
    mkt, _, _, spider = _setup()
    assert mkt.treasury.total_minted == 0 and mkt.treasury.issuances == []
    assert spider.balance(NATIVE) == 0      # a fresh spider holds nothing


@pytest.mark.property
def test_correct_result_pays_the_bounded_integer_reward():
    mkt, ad, client, spider = _setup(rate_num=1, rate_den=2)
    job = ComputeJob(job_id="job-ok", seed=b"deterministic-input", n_blocks=N_BLOCKS)

    escrow = job.escrow(ad)                  # 16 blocks * 1 = 16 PLS
    expected_reward = escrow * 1 // 2        # bounded mint = escrow/2 = 8
    before_spider = spider.balance(NATIVE)

    r = mkt.run_job(job, ad, client, spider, submit_beat=SUBMIT_BEAT)

    assert r.confirmed and r.released and not r.slashed
    assert r.reward == expected_reward       # integer reward, demand-gated
    assert r.k >= 1 and len(r.committee) == 5 and spider.address not in r.committee
    # spider gains: the settled escrow + the minted reward.
    assert spider.balance(NATIVE) == before_spider + escrow + expected_reward
    assert mkt.treasury.total_minted == expected_reward
    assert spider.braid.validate()           # coinbase keeps the braid valid


@pytest.mark.property
def test_tampered_result_is_rejected_and_earns_nothing():
    mkt, ad, client, spider = _setup()
    job = ComputeJob(job_id="job-bad", seed=b"deterministic-input", n_blocks=N_BLOCKS)

    client_before = client.balance(NATIVE)
    spider_before = spider.balance(NATIVE)

    r = mkt.run_job(job, ad, client, spider, submit_beat=SUBMIT_BEAT, tamper=True)

    assert not r.confirmed and not r.released and r.slashed
    assert r.reward == 0 and r.issuance is None
    # Nothing settled, nothing minted: the cheating spider earns zero.
    assert spider.balance(NATIVE) == spider_before
    assert client.balance(NATIVE) == client_before
    assert mkt.treasury.total_minted == 0
    # The stake was slashed and the escrow refunded in the dispute ledger.
    assert mkt.ledger.collateral_slashed == mkt.required_stake(job, ad)
    assert mkt.ledger.escrow_refunded == job.escrow(ad)


@pytest.mark.property
def test_pls_conserved_and_demand_gated_no_premine():
    mkt, ad, client, spider = _setup(rate_num=1, rate_den=2)
    job = ComputeJob(job_id="job-conserve", seed=b"seed-xyz", n_blocks=N_BLOCKS)

    total_before = mkt.total_supply(client, spider)
    assert mkt.treasury.total_minted == 0           # no premine

    r = mkt.run_job(job, ad, client, spider, submit_beat=SUBMIT_BEAT)
    total_after = mkt.total_supply(client, spider)

    # Supply grows by EXACTLY the bounded reward — escrow merely moved client→spider.
    assert total_after == total_before + r.reward
    assert r.reward == mkt.treasury.total_minted
    # Demand bound: the mint never exceeds the escrow the client actually spent.
    assert r.reward <= r.escrow


@pytest.mark.property
def test_mint_never_exceeds_escrow_demand():
    # Even a >100% emission rate is clamped to the escrow (demand bound), still integer.
    mkt, ad, client, spider = _setup(rate_num=10, rate_den=1)
    job = ComputeJob(job_id="job-clamp", seed=b"seed-clamp", n_blocks=N_BLOCKS)

    r = mkt.run_job(job, ad, client, spider, submit_beat=SUBMIT_BEAT)
    assert r.confirmed
    assert r.reward == r.escrow                      # clamped to escrow, not 10x
    assert mkt.treasury.total_minted == r.escrow


@pytest.mark.property
def test_spider_must_cover_capacity():
    mkt, ad, client, spider = _setup()
    # A job needing more GPUs than advertised is rejected before any settlement.
    job = ComputeJob(job_id="job-toobig", seed=b"s", n_blocks=4, need_gpus=99)
    with pytest.raises(ValueError, match="capacity"):
        mkt.run_job(job, ad, client, spider, submit_beat=SUBMIT_BEAT)
