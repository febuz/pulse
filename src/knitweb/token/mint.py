"""PLS issuance — demand-gated, bounded minting via proof-of-useful-work.

This closes the open question the PoUW layer deferred (`pouw/escrow.py`,
`pouw/job.py`): how new PLS comes into existence. The rules, all integer and
provable:

  * **No premine, no admin mint.** Native PLS is created *only* as a reward for
    verified useful work. A fresh :class:`Treasury` has minted nothing.
  * **Demand-gated.** Issuance happens only when a PoUW proof verifies (sampled
    re-execution, `pouw.job.verify`). A fraudulent proof settles nothing and mints
    nothing (and is slashable).
  * **Bounded.** The minted reward never exceeds the escrow the consumer actually
    spent (mint ≤ proven economic demand), and total native issuance never exceeds
    an optional hard ``max_supply`` cap.
  * **Conserved + auditable.** Each mint is a *coinbase* Fiber appended to the
    worker's Braid, tagged with the issuance CID — so the Braid's existing
    spent-knit guard makes a given issuance un-replayable (no double-mint), and the
    :class:`Treasury` tracks cumulative supply for audit.

The full economic loop is :meth:`Treasury.reward_verified_work`: verify → settle the
consumer's escrow to the worker (conservation-preserving) → mint the bounded reward.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical
from ..ledger import blob
from ..ledger.fiber import Fiber
from ..ledger.node import AccountNode
from ..pouw.job import SynapticCompileJob, WorkProof, verify

__all__ = ["NATIVE", "EmissionPolicy", "Issuance", "Treasury"]

NATIVE = "PLS"


@dataclass(frozen=True)
class EmissionPolicy:
    """Bounded, demand-gated emission schedule (integer-only).

    The reward for a verified job is ``escrow * rate_num // rate_den``, then clamped
    so it never exceeds the escrow itself (mint ≤ proven demand) nor pushes cumulative
    issuance past ``max_supply``. ``rate_num/rate_den`` defaults to a 1/2 work subsidy.
    """

    rate_num: int = 1
    rate_den: int = 2
    max_supply: int | None = None

    def __post_init__(self) -> None:
        if self.rate_den <= 0 or self.rate_num < 0:
            raise ValueError("emission rate must have rate_num>=0 and rate_den>0")
        if self.max_supply is not None and self.max_supply < 0:
            raise ValueError("max_supply must be non-negative")

    def reward(self, escrow: int, already_minted: int) -> int:
        """The bounded reward for a job whose consumer spent ``escrow`` pulses."""
        if escrow <= 0:
            return 0
        r = (escrow * self.rate_num) // self.rate_den
        r = min(r, escrow)  # demand bound: never mint more than the escrow consumed
        if self.max_supply is not None:
            r = min(r, max(0, self.max_supply - already_minted))
        return r


@dataclass(frozen=True)
class Issuance:
    """An auditable record of one bounded mint."""

    worker: str       # PLS address of the rewarded worker
    amount: int       # PLS-wei minted (0 means "settled but capped out")
    escrow: int       # the escrow that gated this issuance
    job_digest: str   # digest of the verified work proof
    timestamp: int

    def to_record(self) -> dict:
        return {
            "kind": "pls-issuance",
            "worker": self.worker,
            "amount": self.amount,
            "escrow": self.escrow,
            "job_digest": self.job_digest,
            "timestamp": self.timestamp,
        }

    @property
    def cid(self) -> str:
        return canonical.cid(self.to_record())


class Treasury:
    """The native-PLS issuer. Mints only via verified PoUW; tracks cumulative supply.

    There is intentionally **no** raw, ungated ``mint`` method exposed: the only way
    to create native PLS is :meth:`reward_verified_work`, which proves the work first.
    """

    def __init__(self, policy: EmissionPolicy | None = None) -> None:
        self.policy = policy or EmissionPolicy()
        self.total_minted = 0
        self.issuances: list[Issuance] = []
        self._rewarded_digests: set[str] = set()  # work already rewarded (anti-replay)

    def reward_verified_work(
        self,
        consumer: AccountNode,
        worker: AccountNode,
        escrow: int,
        job: SynapticCompileJob,
        proof: WorkProof,
        timestamp: int,
    ) -> Issuance | None:
        """Run the full PoUW economic loop. Returns the Issuance, or None on fraud.

        1. **Gate** on sampled re-execution (`pouw.job.verify`). Fraud ⇒ None,
           nothing settles, nothing mints.
        2. **Anti-replay**: a given piece of work (its proof digest) is rewarded at
           most once. Without this a colluding consumer+worker could resubmit the
           same proof to mint unboundedly (escrow merely cycles between them) — the
           "no infinite mint" soundness requirement. A duplicate ⇒ None, no-op.
        3. **Settle** the consumer's ``escrow`` to the worker (a normal Knit
           transfer — conservation-preserving, no issuance).
        4. **Mint** the bounded reward to the worker as a coinbase, record it.
        """
        if escrow < 0:
            raise ValueError("escrow must be non-negative")
        if not verify(job, proof):
            return None
        if proof.digest in self._rewarded_digests:
            return None  # this work was already rewarded — no replay, no double-mint
        self._rewarded_digests.add(proof.digest)

        # 3. settle escrow consumer -> worker (conservation-preserving)
        if escrow > 0:
            consumer.transfer_to(worker, NATIVE, escrow, timestamp)

        # 4. bounded mint
        amount = self.policy.reward(escrow, self.total_minted)
        issuance = Issuance(
            worker=worker.address,
            amount=amount,
            escrow=escrow,
            job_digest=proof.digest,
            timestamp=timestamp,
        )
        if amount > 0:
            self._coinbase(worker, amount, issuance)
            self.total_minted += amount
        self.issuances.append(issuance)
        return issuance

    def _coinbase(self, worker: AccountNode, amount: int, issuance: Issuance) -> Fiber:
        """Append a coinbase Fiber crediting ``amount`` PLS to ``worker``.

        The Fiber's ``knit`` field is the issuance CID, so the Braid's spent-knit
        guard rejects any attempt to replay the same issuance (no double-mint). The
        worker's nonce is unchanged — minting does not consume a transfer nonce.
        """
        head = worker.braid.head
        new_balances = blob.credit(head.balances, NATIVE, amount)
        coinbase = Fiber(
            owner=worker.pub,
            seq=head.seq + 1,
            balances=new_balances,
            nonce=head.nonce,
            prev=head.cid,
            knit=issuance.cid,
        )
        return worker.braid.weave(coinbase)
