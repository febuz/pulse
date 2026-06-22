"""Spider PoUW compute marketplace — the end-to-end DePIN flow, composed from the
existing PoUW primitives (no new scheduling/verification/escrow/mint is invented here).

A *spider* advertises spare GPU/RAM; a *client* submits a **bounded** deterministic
compute job with escrow + staked collateral; the scheduler admits it under the
compute guardrail; the spider executes and commits to its output; a verifier
**committee** re-executes a sample and votes; on a confirm quorum the escrow releases
and the spider earns a **demand-gated, bounded** PLS mint. A wrong/tampered result is
caught by the committee, slashes the stake, and earns nothing.

This module is glue, not new mechanism. Every stage delegates to a shipped primitive:

  ADVERTISE  → :class:`SpiderAd` (a plain capacity record; no economics)
  SCHEDULE   → :class:`~knitweb.pouw.scheduler.GpuScheduler` (the compute guardrail)
               + :class:`~knitweb.pouw.dispute.DisputeWindowLedger.submit`
               (escrow + collateral, sized via :mod:`knitweb.pouw.collateral`)
  EXECUTE    → a tiny **deterministic** compute → output blocks → ``challenge.commit``
  VERIFY     → ``verify.plan_verification`` + ``verify.run_committee`` +
               ``dispute.dispute_by_quorum`` (``quorum``/``challenge``/``sampling``/``committee``)
  REWARD     → ``dispute.release`` (escrow settles) + ``token.mint.Treasury``
               (demand-gated, bounded, no-premine PLS issuance)

Determinism is the whole game: the spider's compute is a pure function of the job
spec, so a verifier re-running it gets byte-identical output — exactly the soundness
``pouw/job`` relies on. Money & state stay integer PLS-wei throughout; no float ever
touches the hashed/committed bytes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from fractions import Fraction
from typing import List

from ..ledger.node import AccountNode
from ..token.mint import NATIVE, EmissionPolicy, Issuance, Treasury
from . import challenge, verify
from .collateral import Margin, required_collateral
from .dispute import DisputeWindowLedger
from .job import SynapticCompileJob, WorkProof, execute as _execute_job
from .scheduler import GpuScheduler

__all__ = [
    "SpiderAd",
    "ComputeJob",
    "MarketResult",
    "Marketplace",
]


# ── ADVERTISE ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SpiderAd:
    """A spider advertising spare capacity for sale (a plain record, no economics).

    Capacities are integers (GPU count, MiB of RAM) — no floats near the wire/hash.
    """

    spider: str          # the spider's PLS address / peer id
    gpus: int            # number of GPUs offered
    ram_mib: int         # RAM offered, in MiB
    price_per_block: int = 1   # asking price (PLS-wei) per output block of work

    def __post_init__(self) -> None:
        for name in ("gpus", "ram_mib", "price_per_block"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise TypeError(f"{name} must be int")
            if v < 0:
                raise ValueError(f"{name} must be >= 0")
        if not isinstance(self.spider, str) or not self.spider:
            raise TypeError("spider must be a non-empty str")

    def can_serve(self, job: "ComputeJob") -> bool:
        """True iff this spider's advertised capacity covers the job's demand."""
        return self.gpus >= job.need_gpus and self.ram_mib >= job.need_ram_mib


# ── SUBMIT ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComputeJob:
    """A bounded, deterministic compute a client wants done.

    The "compute" is intentionally tiny — the point is the *protocol* flow, not a real
    GPU kernel. Each of ``n_blocks`` output blocks is a pure function of ``(seed, i)``,
    so re-execution is byte-deterministic (what ``pouw`` verification rests on). The job
    is bounded by construction: ``n_blocks`` (and the scheduler slot) keep it minutes,
    not hours.
    """

    job_id: str
    seed: bytes
    n_blocks: int
    need_gpus: int = 1
    need_ram_mib: int = 256

    def __post_init__(self) -> None:
        if not isinstance(self.job_id, str) or not self.job_id:
            raise TypeError("job_id must be a non-empty str")
        if not isinstance(self.seed, (bytes, bytearray)) or not self.seed:
            raise TypeError("seed must be non-empty bytes")
        for name in ("n_blocks", "need_gpus", "need_ram_mib"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool):
                raise TypeError(f"{name} must be int")
        if self.n_blocks < 1:
            raise ValueError("n_blocks must be >= 1 (a job must produce output)")

    def escrow(self, ad: SpiderAd) -> int:
        """The integer PLS-wei a client escrows for this job at the spider's price."""
        return self.n_blocks * ad.price_per_block


# ── the deterministic bounded compute ────────────────────────────────────────


def _compute_blocks(job: ComputeJob) -> List[bytes]:
    """The spider's (or a verifier's) bounded deterministic compute.

    Block ``i`` is ``sha256(seed || i)`` — a pure function of the job spec, so any
    honest party reproduces byte-identical blocks. (Stand-in for a real GPU kernel;
    the protocol around it is what matters.)
    """
    return [
        hashlib.sha256(bytes(job.seed) + i.to_bytes(8, "big")).digest()
        for i in range(job.n_blocks)
    ]


def _as_synaptic_job(job: ComputeJob, blocks: List[bytes]) -> tuple:
    """Bridge the bounded compute into a ``SynapticCompileJob`` so the **mint**'s
    demand-gate (``token.mint`` → ``pouw.job.verify``, a real deterministic
    re-execution) checks the *same* output the committee checked.

    The compute output is encoded as content-addressed relations of a throwaway
    OriginTrail-style asset; the originator key is derived deterministically from the
    job seed (test/bridge identity — see ``AccountNode.from_seed``). Returns
    ``(SynapticCompileJob, originator_priv)``.
    """
    from ..core import crypto

    orig_priv = hashlib.sha256(b"knitweb:marketplace:originator:" + bytes(job.seed)).hexdigest()
    orig_pub = crypto.public_from_private(orig_priv)
    asset = {
        "origintrail_id": job.job_id,
        "originator": orig_pub,
        "@graph": [
            {"subject": job.job_id, "predicate": f"block:{i}", "object": b.hex()}
            for i, b in enumerate(blocks)
        ],
    }
    return SynapticCompileJob(asset=asset, originator_pub=orig_pub), orig_priv


# ── result of one marketplace round ───────────────────────────────────────────


@dataclass
class MarketResult:
    """The auditable outcome of one advertise→schedule→execute→verify→reward round."""

    job_id: str
    confirmed: bool
    released: bool
    slashed: bool
    reward: int                 # PLS-wei minted to the spider (0 unless confirmed)
    escrow: int                 # PLS-wei the client committed
    committee: List[str] = field(default_factory=list)
    k: int = 0
    issuance: "Issuance | None" = None


# ── the marketplace ───────────────────────────────────────────────────────────


class Marketplace:
    """Composes the shipped PoUW primitives into the end-to-end compute market.

    Holds the shared, long-lived pieces: the compute guardrail (``GpuScheduler``), the
    settlement-timing ledger (``DisputeWindowLedger``, with collateral enforcement on),
    and the native-PLS ``Treasury`` (demand-gated, bounded, no premine). Verifier
    economics (committee size, audit confidence) are policy knobs, not new mechanism.
    """

    def __init__(
        self,
        *,
        treasury: Treasury | None = None,
        dispute_window: int = 5,
        release_delay: int = 8,
        committee_size: int = 5,
        corrupt_hypothesis: int = 1,
        max_miss: Fraction = Fraction(1, 100),
        margin: Margin | None = None,
        max_concurrent: int = 1,
    ) -> None:
        self.scheduler = GpuScheduler(max_concurrent=max_concurrent)
        self.ledger = DisputeWindowLedger(
            dispute_window=dispute_window,
            release_delay=release_delay,
            enforce_collateral=True,
            margin=margin or Margin(1, 1),
        )
        self.treasury = treasury or Treasury(EmissionPolicy(rate_num=1, rate_den=2))
        self.committee_size = committee_size
        self.corrupt_hypothesis = corrupt_hypothesis
        self.max_miss = max_miss
        self.release_delay = release_delay
        self._ads: dict[str, SpiderAd] = {}
        self._eligible_verifiers: List[str] = []

    # ADVERTISE
    def advertise(self, ad: SpiderAd, verifier_pool: List[str]) -> None:
        """A spider advertises capacity; ``verifier_pool`` seeds the eligible jury."""
        self._ads[ad.spider] = ad
        for v in verifier_pool:
            if v not in self._eligible_verifiers:
                self._eligible_verifiers.append(v)

    def required_stake(self, job: ComputeJob, ad: SpiderAd) -> int:
        """Collateral (PLS-wei) a spider must stake to back this job's escrow at risk."""
        return required_collateral(job.escrow(ad), self.ledger.margin)

    # the full round: SCHEDULE → EXECUTE → VERIFY → REWARD
    def run_job(
        self,
        job: ComputeJob,
        ad: SpiderAd,
        client: AccountNode,
        spider: AccountNode,
        *,
        submit_beat: int,
        tamper: bool = False,
    ) -> MarketResult:
        """Run one bounded job end-to-end and settle it.

        ``tamper=True`` makes the spider return a WRONG result (a corrupted output
        block): the committee must catch it, the stake is slashed, and the spider earns
        nothing. With ``tamper=False`` an honest spider is confirmed, the escrow
        releases, and the demand-gated bounded mint pays the reward.
        """
        if not ad.can_serve(job):
            raise ValueError("spider cannot serve this job's capacity demand")

        escrow = job.escrow(ad)
        collateral = self.required_stake(job, ad)

        # EXECUTE under the compute guardrail (bounded slot). The honest output is what
        # a verifier independently recomputes; a tampering spider commits to wrong bytes.
        honest_blocks = _compute_blocks(job)
        with self.scheduler.slot():
            worker_blocks = list(honest_blocks)
            if tamper:
                worker_blocks[0] = b"TAMPERED" + worker_blocks[0][8:]

        # SUBMIT: escrow + staked collateral enter the dispute-window ledger.
        self.ledger.submit(
            job.job_id, spider.address, client.address, escrow, collateral, submit_beat
        )

        # VERIFY: select+size a committee and run sampled re-execution against the
        # spider's committed output (challenge/sampling/committee/quorum/verify).
        commitment = challenge.commit(worker_blocks)
        plan = verify.plan_verification(
            commitment.root,                       # unpredictable per-job seed (commit-derived)
            self._eligible_verifiers,
            spider.address,
            job.n_blocks,
            committee_size=self.committee_size,
            corrupt_hypothesis=self.corrupt_hypothesis,
            max_miss=self.max_miss,
        )
        salts = [f"salt::{job.job_id}::{v}".encode() for v in plan.committee]
        verdicts = verify.run_committee(
            commitment, worker_blocks, honest_blocks, salts, plan.k
        )

        slashed, _ = self.ledger.dispute_by_quorum(job.job_id, verdicts, beat=submit_beat + 1)
        confirmed = all(v.value == "confirm" for v in verdicts) and not slashed

        result = MarketResult(
            job_id=job.job_id,
            confirmed=confirmed,
            released=False,
            slashed=slashed,
            reward=0,
            escrow=escrow,
            committee=plan.committee,
            k=plan.k,
        )
        if not confirmed:
            # WRONG result rejected: stake slashed, escrow refunded, no settle, no mint.
            return result

        # REWARD: the window has cleared (release_delay > dispute_window), so the escrow
        # releases to the spider AND the treasury mints the bounded, demand-gated reward.
        released, _ = self.ledger.release(job.job_id, beat=submit_beat + self.release_delay)
        result.released = released

        # The mint re-gates on the SAME output via pouw.job.verify (deterministic
        # re-execution): it settles the client's escrow to the spider and mints
        # bounded PLS (≤ escrow, ≤ max_supply, no premine). One issuance per job digest.
        syn_job, orig_priv = _as_synaptic_job(job, worker_blocks)
        proof: WorkProof = _execute_job(syn_job, orig_priv)
        issuance = self.treasury.reward_verified_work(
            client, spider, escrow, syn_job, proof, timestamp=submit_beat
        )
        result.issuance = issuance
        result.reward = issuance.amount if issuance is not None else 0
        return result

    def total_supply(self, *accounts: AccountNode) -> int:
        """Sum of native PLS held across ``accounts`` (for conservation checks)."""
        return sum(a.balance(NATIVE) for a in accounts)
