"""End-to-end PoUW verification flow — select a jury, size the audit, re-execute, vote.

This is the capstone that composes the standalone PoUW primitives into the runnable verification
path; until now each existed in isolation:

  committee.select_committee  (jury)   →  *who* verifies a job, unpredictably yet verifiably
  sampling.required_samples   (audit)  →  *how many* output blocks each verifier re-checks
  challenge.verify_response    (proof) →  the worker's revealed blocks are authentic to its commit
  + re-execution comparison            →  those blocks actually match an honest re-execution
  ⇒ a quorum.Verdict per verifier      →  the exact input quorum.tally / dispute_by_quorum consumes

So this module is the **upstream** half (produce the verdict stream) and
``pouw/dispute.DisputeWindowLedger.dispute_by_quorum`` is the **downstream** half (slash/settle on
it). A verifier's verdict is grounded in re-execution: it independently recomputes the work and
**CONFIRM**s only if every sampled block it checked matches both the worker's signed commitment
*and* its own honest recompute; any divergence — a forged reveal or a wrong block — is a
**MISMATCH**. Sizing ``k`` via ``sampling`` is what makes a sampled (not full) re-check sound: the
bigger the hypothesised corruption, the fewer samples needed to catch it with target confidence.

Deterministic and integer/hash only; no canonical/signed-record changes (it *reads* commitments
and reveals, never mints records).
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from typing import Any, List

from . import challenge
from .committee import select_committee
from .quorum import Verdict
from .sampling import required_samples

__all__ = [
    "VerificationPlan",
    "plan_verification",
    "verifier_verdict",
    "run_committee",
    # IL-106 — distill re-execution check
    "DistillReexecResult",
    "verify_distill",
]


@dataclass(frozen=True)
class VerificationPlan:
    """Who checks the job (``committee``) and how many blocks each samples (``k``)."""

    committee: List[str]
    k: int


def plan_verification(
    seed: bytes,
    eligible: List[str],
    worker: str,
    n_blocks: int,
    *,
    committee_size: int,
    corrupt_hypothesis: int,
    max_miss: Fraction,
) -> VerificationPlan:
    """Pick the verifier committee (#58) and the per-verifier sample count ``k`` (#55).

    ``k`` is sized to catch a worker that corrupted ``corrupt_hypothesis`` of ``n_blocks`` with
    miss probability ≤ ``max_miss`` (then clamped to ``n_blocks``). The worker is excluded from
    its own jury.
    """
    committee = select_committee(seed, eligible, committee_size, exclude=worker)
    k = min(required_samples(n_blocks, corrupt_hypothesis, max_miss), n_blocks)
    return VerificationPlan(committee=committee, k=k)


def verifier_verdict(
    commitment: challenge.Commitment,
    salt: bytes,
    k: int,
    reveals: List[challenge.Reveal],
    recomputed_blocks: List[bytes],
) -> Verdict:
    """One verifier's verdict over its ``k`` sampled blocks (re-execution spot-check).

    ``CONFIRM`` iff (a) the reveals authentically answer *this* salt against the worker's signed
    ``commitment`` (``challenge.verify_response`` — no work-swap, no precompute) **and** (b) every
    revealed block equals the verifier's own honest re-execution at that index. Otherwise
    ``MISMATCH`` — a forged reveal or a block that differs from a correct recompute.
    """
    if not challenge.verify_response(commitment, salt, k, reveals):
        return Verdict.MISMATCH
    for r in reveals:
        if r.index >= len(recomputed_blocks) or r.block != recomputed_blocks[r.index]:
            return Verdict.MISMATCH
    return Verdict.CONFIRM


def run_committee(
    commitment: challenge.Commitment,
    worker_blocks: List[bytes],
    recomputed_blocks: List[bytes],
    salts: List[bytes],
    k: int,
) -> List[Verdict]:
    """Drive a committee: each verifier draws its own ``salt``, the worker answers, the verifier
    votes. Returns one :class:`~knitweb.pouw.quorum.Verdict` per salt — feed it straight to
    ``quorum.tally`` / ``dispute_by_quorum``.

    Models the protocol honestly: the worker reveals the blocks it actually committed
    (``worker_blocks``) for each verifier's distinct salt, and each verifier compares against its
    independent ``recomputed_blocks``. If the worker cheated (``worker_blocks`` diverges from a
    correct recompute), a verifier whose sample hits a diverging index votes ``MISMATCH`` — which
    is exactly why ``k`` (sized by ``sampling``) governs detection.
    """
    verdicts: List[Verdict] = []
    for salt in salts:
        reveals = challenge.respond(worker_blocks, salt, k)
        verdicts.append(verifier_verdict(commitment, salt, k, reveals, recomputed_blocks))
    return verdicts


# ---------------------------------------------------------------------------
# IL-106 — deterministic re-execution of retrieve + gate for distill jobs.
#
# Model-guided distillation is NOT byte-reproducible, so verifiers cannot
# byte-compare its output.  They CAN deterministically re-run the two halves
# that ARE reproducible:
#
#   1. retrieve(query, subscription, web, web_state_cid=...) → candidate set
#   2. gate: every relation in the bundle must (a) have all three CIDs in the
#      candidate set AND (b) pass the attestation/provenance gate
#
# A mismatch in either half means the worker fabricated evidence — the
# bundle contains relations that were not reachable from the input query or
# that would have been dropped by the gate.  This is a slash-worthy offence.
#
# The function injects both the retrieve callable and the gate callable so the
# pouw layer stays import-free of the interpret layer at module load time.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DistillReexecResult:
    """Outcome of a deterministic re-execution check for one distill bundle.

    ``deterministic_ok`` is the signal :data:`~knitweb.pouw.job.split_settles`
    consumes.  The remaining fields expose which relation failed so callers can
    log a targeted slash reason.
    """

    deterministic_ok: bool
    candidate_mismatch: bool
    gate_failure: bool
    first_bad_relation: object | None


def verify_distill(
    manifest,                 # DistillManifest — duck-typed to avoid circular import
    bundle_relations,         # tuple/list of synaptic.Relation objects
    web,                      # knitweb.fabric.web.Web pinned at manifest.web_state_cid
    *,
    retrieve_fn,              # callable matching retrieve(query, subscription, web, *, web_state_cid)
    gate_fn,                  # callable matching gate_relations(relations, candidates, web)
    original_query,           # the pre-image of manifest.query (needed to re-run retrieve)
) -> DistillReexecResult:
    """Re-run the deterministic halves of a distill job and return a recheck verdict.

    The two deterministic checks (AC1 and AC2 of IL-106):

    1. **Candidate check**: re-run ``retrieve`` against the pinned ``web_state_cid``.
       Every relation's subject/predicate/obj must be a CID in the re-derived
       candidate set.  A relation whose CIDs are absent from the candidate set was
       fabricated — the worker could not have encountered it through an honest
       retrieve run.

    2. **Gate check**: re-run the provenance gate on every relation.  Any relation
       that passes the candidate check but fails the gate was emitted in defiance
       of the gate rules — also fraudulent.

    ``deterministic_ok = True`` iff BOTH checks pass for ALL relations.

    Parameters
    ----------
    manifest
        A ``DistillManifest``-like object with ``.subscription``,
        ``.web_state_cid``, and ``.query`` attributes.
    bundle_relations
        The relations the worker claims are in the signed bundle.
    web
        The ``Web`` snapshot at ``manifest.web_state_cid`` (caller is responsible
        for pinning the correct epoch; this function does not re-fetch the web).
    retrieve_fn
        The retrieve callable (injected so pouw does not import interpret at load
        time): ``retrieve_fn(query, subscription, web, *, web_state_cid) → CandidateSet``.
    gate_fn
        The gate callable: ``gate_fn(relations, candidates, web) → tuple[Relation, ...]``.
    original_query
        The pre-image query.  A verifier confirms it fingerprints to ``manifest.query``
        before calling this function.
    """
    # Re-derive the candidate set deterministically.
    try:
        candidate_set = retrieve_fn(
            original_query,
            manifest.subscription or None,
            web,
            web_state_cid=manifest.web_state_cid,
        )
    except Exception:
        # If retrieve fails against the pinned web state the manifest is invalid.
        return DistillReexecResult(
            deterministic_ok=False,
            candidate_mismatch=True,
            gate_failure=False,
            first_bad_relation=None,
        )

    candidate_cids: frozenset[str] = frozenset(candidate_set.cids)

    # AC1 — every relation's CIDs must be in the re-derived candidate set.
    for relation in bundle_relations:
        for cid in (relation.subject, relation.predicate, relation.obj):
            if cid not in candidate_cids:
                return DistillReexecResult(
                    deterministic_ok=False,
                    candidate_mismatch=True,
                    gate_failure=False,
                    first_bad_relation=relation,
                )

    # AC2 — every relation must also pass the attestation/provenance gate.
    gated = gate_fn(list(bundle_relations), candidate_set, web)
    gated_keys = {(r.subject, r.predicate, r.obj) for r in gated}
    for relation in bundle_relations:
        if (relation.subject, relation.predicate, relation.obj) not in gated_keys:
            return DistillReexecResult(
                deterministic_ok=False,
                candidate_mismatch=False,
                gate_failure=True,
                first_bad_relation=relation,
            )

    return DistillReexecResult(
        deterministic_ok=True,
        candidate_mismatch=False,
        gate_failure=False,
        first_bad_relation=None,
    )
