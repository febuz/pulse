"""Proof-of-Useful-Work: the synaptic-compile job + sampled re-execution.

This is the economic heart of Fiber. A consumer wants a verified relation bundle
(OriginTrail asset → signed synaptic bytecode); a spider does the work; peers
*re-execute a sample* of the work to confirm it, then the consumer's escrowed
pulses settle to the spider.

Soundness rests on **determinism**: compiling the same OriginTrail asset always
yields byte-identical bytecode (the canonical synaptic compiler guarantees this),
so a verifier re-runs the job and checks the result digest matches — no trust in
the spider required. The heavy work (resolve + compile) stays off the ledger; only
the integer verdict (match? signature valid?) touches settlement.

Issuance note: this module settles work by **transferring** the consumer's escrow
to the worker (conservation-preserving) — it never mints. New PLS *issuance* is
handled separately by `token/mint.py` (demand-gated, bounded per Pulse epoch;
shipped in #17). Escrow settlement here is the proven subset; mint stays off this
path.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..synaptic import bytecode as _bc
from ..synaptic.origintrail import resolve_asset

__all__ = ["SynapticCompileJob", "WorkProof", "execute", "verify"]


@dataclass(frozen=True)
class SynapticCompileJob:
    """A unit of useful work: compile an OriginTrail asset to signed bytecode."""

    asset: dict
    originator_pub: str   # the verified originator whose signature must appear


@dataclass(frozen=True)
class WorkProof:
    """What a spider emits after doing the work."""

    bytecode: bytes
    signature: str        # originator signature over the bytecode
    digest: str           # claimed content digest of the bytecode


def execute(job: SynapticCompileJob, originator_priv: str) -> WorkProof:
    """Do the work: resolve the asset, compile to bytecode, sign, digest."""
    asset_id, originator, relations = resolve_asset(job.asset)
    data = _bc.compile_bundle(asset_id, originator, relations)
    return WorkProof(
        bytecode=data,
        signature=_bc.sign_bundle(originator_priv, data),
        digest=_bc.bundle_digest(data),
    )


def verify(job: SynapticCompileJob, proof: WorkProof) -> bool:
    """Sampled re-execution: independently redo the job and confirm the proof.

    Checks, all deterministic/boolean:
      1. the claimed digest matches the claimed bytecode,
      2. re-compiling the asset reproduces byte-identical bytecode (the work was
         done honestly — determinism makes this a real check, not a guess),
      3. the originator signature is valid over the bytecode.
    Any failure ⇒ the proof is fraudulent and must not settle (and is slashable).
    """
    if _bc.bundle_digest(proof.bytecode) != proof.digest:
        return False
    asset_id, originator, relations = resolve_asset(job.asset)
    recompiled = _bc.compile_bundle(asset_id, originator, relations)
    if recompiled != proof.bytecode:
        return False
    return _bc.verify_bundle(job.originator_pub, proof.bytecode, proof.signature)
