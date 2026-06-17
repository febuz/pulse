"""Demand-gated settlement: pay a spider only for verified useful work.

The consumer commits pulses to a job; the spider does the work; a verifier
re-executes a sample (``pouw.job.verify``); only on success do the pulses settle
from consumer to worker. A fraudulent proof settles nothing — and is slashable.

Settlement is a conservation-preserving Knit transfer (no new issuance here), so
total PLS is unchanged: this is the sound subset of the economic loop we can prove
today, independent of the deferred mint/bootstrap-emission policy.
"""

from __future__ import annotations

from ..ledger.node import AccountNode
from .job import SynapticCompileJob, WorkProof, verify

__all__ = ["settle_on_verify"]


def settle_on_verify(
    consumer: AccountNode,
    worker: AccountNode,
    pulses: int,
    job: SynapticCompileJob,
    proof: WorkProof,
    timestamp: int,
) -> bool:
    """Pay ``pulses`` (PLS) from ``consumer`` to ``worker`` iff ``proof`` verifies.

    Returns True when the work was confirmed and paid, False when the proof failed
    verification (no payment occurs, so a bad spider earns nothing).
    """
    if not verify(job, proof):
        return False
    consumer.transfer_to(worker, "PLS", pulses, timestamp)
    return True
