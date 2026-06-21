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
from ..core.pulse import Pulse
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
    so it never exceeds the escrow itself (mint ≤ proven demand), nor pushes cumulative
    issuance past ``max_supply``, nor exceeds the remaining ``epoch_cap`` for the Pulse
    epoch the mint falls in. ``rate_num/rate_den`` defaults to a 1/2 work subsidy.

    ``epoch_cap`` is the per-epoch supply ceiling (PLS-wei minted within one Pulse
    epoch). ``None`` (default) means epoch issuance is unbounded — behaviour is then
    identical to the pre-epoch policy. Binding a cap to the heartbeat is how the
    Pulse governs the money supply: activity (Beats) gates issuance rate.
    """

    rate_num: int = 1
    rate_den: int = 2
    max_supply: int | None = None
    epoch_cap: int | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.rate_num, int)
            or isinstance(self.rate_num, bool)
            or not isinstance(self.rate_den, int)
            or isinstance(self.rate_den, bool)
        ):
            raise TypeError("emission rate numerator/denominator must be int")
        if self.rate_den <= 0 or self.rate_num < 0:
            raise ValueError("emission rate must have rate_num>=0 and rate_den>0")
        if self.max_supply is not None:
            if not isinstance(self.max_supply, int) or isinstance(self.max_supply, bool):
                raise TypeError("max_supply must be int")
            if self.max_supply < 0:
                raise ValueError("max_supply must be non-negative")
        if self.epoch_cap is not None:
            if not isinstance(self.epoch_cap, int) or isinstance(self.epoch_cap, bool):
                raise TypeError("epoch_cap must be int")
            if self.epoch_cap < 0:
                raise ValueError("epoch_cap must be non-negative")

    def reward(
        self, escrow: int, already_minted: int, epoch_remaining: int | None = None
    ) -> int:
        """The bounded reward for a job whose consumer spent ``escrow`` pulses.

        ``epoch_remaining`` (when not ``None``) is the PLS-wei still mintable in the
        current Pulse epoch; the reward is additionally clamped to it. ``None`` leaves
        the result unchanged, so a treasury without epoch binding behaves exactly as
        before (this method is a pure superset of the prior bound).
        """
        if escrow <= 0:
            return 0
        r = (escrow * self.rate_num) // self.rate_den
        r = min(r, escrow)  # demand bound: never mint more than the escrow consumed
        if self.max_supply is not None:
            r = min(r, max(0, self.max_supply - already_minted))
        if epoch_remaining is not None:
            r = min(r, max(0, epoch_remaining))  # per-epoch supply ceiling (Pulse-gated)
        return r


@dataclass(frozen=True)
class Issuance:
    """An auditable record of one bounded mint."""

    worker: str       # PLS address of the rewarded worker
    amount: int       # PLS-wei minted (0 means "settled but capped out")
    escrow: int       # the escrow that gated this issuance
    job_digest: str   # digest of the verified work proof
    timestamp: int
    # Pulse epoch this mint fell in (None when the treasury is not epoch-bound).
    # Audit/accounting only — DELIBERATELY excluded from to_record()/cid so the
    # issuance's canonical bytes (and thus the coinbase Fiber's knit CID) are
    # byte-identical to the pre-epoch path. Adding it here would change every
    # issuance hash; keeping it off the canonical record is the byte-identity guard.
    epoch: int | None = None

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

    def __init__(
        self, policy: EmissionPolicy | None = None, pulse: Pulse | None = None
    ) -> None:
        self.policy = policy or EmissionPolicy()
        # The Pulse binds issuance to the heartbeat: the epoch a mint falls in is
        # derived from its (injected) timestamp via ``pulse.epoch_at``. ``None``
        # leaves the treasury epoch-unbound — identical to the pre-epoch behaviour.
        self.pulse = pulse
        self.total_minted = 0
        self.issuances: list[Issuance] = []
        self._rewarded_digests: set[str] = set()  # work already rewarded (anti-replay)
        self._epoch_minted: dict[int, int] = {}    # epoch -> PLS-wei minted that epoch

    def epoch_minted(self, epoch: int) -> int:
        """PLS-wei minted in ``epoch`` so far (0 if none / not epoch-bound)."""
        return self._epoch_minted.get(epoch, 0)

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
        if not isinstance(escrow, int) or isinstance(escrow, bool):
            raise TypeError("escrow must be int")
        if escrow < 0:
            raise ValueError("escrow must be non-negative")
        if proof.digest in self._rewarded_digests:
            return None  # this work was already rewarded — no replay, no double-mint
        if escrow > 0:
            if consumer.network != worker.network:
                raise ValueError("consumer and worker must be on the same network")
            if consumer.pub == worker.pub:
                raise ValueError("consumer and worker must differ")
            if consumer.balance(NATIVE) < escrow:
                raise ValueError("consumer balance is below escrow")
        if not verify(job, proof):
            return None

        # 3. settle escrow consumer -> worker (conservation-preserving)
        if escrow > 0:
            consumer.transfer_to(worker, NATIVE, escrow, timestamp)

        # 4. bounded mint — Pulse-gated. The epoch is derived from the injected
        #    timestamp; when both a Pulse and an epoch_cap are set, the reward is
        #    additionally clamped to the supply still mintable in this epoch.
        epoch = self.pulse.epoch_at(timestamp) if self.pulse is not None else None
        epoch_remaining = None
        if epoch is not None:
            # The signed Beat is the consensus-visible monetary governor: prefer the
            # per-epoch cap it carries over the runtime policy default. Capless epochs
            # (no Beat cap) fall back to the policy, preserving the prior behaviour.
            epoch_cap = self.pulse.cap_for_epoch(epoch)
            if epoch_cap is None:
                epoch_cap = self.policy.epoch_cap
            if epoch_cap is not None:
                epoch_remaining = epoch_cap - self._epoch_minted.get(epoch, 0)
        amount = self.policy.reward(escrow, self.total_minted, epoch_remaining)
        issuance = Issuance(
            worker=worker.address,
            amount=amount,
            escrow=escrow,
            job_digest=proof.digest,
            timestamp=timestamp,
            epoch=epoch,
        )
        if amount > 0:
            self._coinbase(worker, amount, issuance)
            self.total_minted += amount
            if epoch is not None:
                self._epoch_minted[epoch] = self._epoch_minted.get(epoch, 0) + amount
        self.issuances.append(issuance)
        self._rewarded_digests.add(proof.digest)
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
