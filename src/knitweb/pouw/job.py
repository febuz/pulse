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

from ..core import canonical
from ..synaptic import bytecode as _bc
from ..synaptic.origintrail import resolve_asset

__all__ = [
    "SynapticCompileJob",
    "WorkProof",
    "execute",
    "verify",
    # IL-105: distill as a PoUW job class with split verification.
    "VERIFICATION_UNIFORM",
    "VERIFICATION_SPLIT",
    "STAGE_MINING",
    "STAGE_SETTLEMENT",
    "JobClass",
    "register_job_class",
    "job_class",
    "verification_policy",
    "DistillManifest",
    "bundle_cid",
    "split_settles",
    "SplitVerdict",
]


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


# --------------------------------------------------------------------------- #
# IL-105 — distill as a PoUW job class with a SPLIT verification policy.       #
#                                                                             #
# The job above (``SynapticCompileJob``) is **uniformly** verified: the work  #
# is byte-reproducible, so a verifier re-executes it and checks an exact /     #
# tolerance digest (``pouw.digest.tolerance_digest`` for GPU work). That does  #
# not fit ``distill`` — model-guided distillation is NOT byte-reproducible, so #
# pretending its output is exactly re-derivable would either reward fraud or   #
# punish honest non-determinism. IL-105 registers ``distill`` as a separate    #
# job class whose reward is settled by a **split** policy: a deterministic     #
# structural re-check (IL-106) AND a challenge window that closes without an    #
# upheld dispute (IL-107 / the existing ``pouw.dispute`` window) must BOTH      #
# clear before any reward settles. The work itself is tagged ``mining``; the   #
# verdict + reward is tagged ``settlement``.                                   #
#                                                                             #
# This module ships the registry, the manifest, and the settlement decision    #
# predicate — all integer/str/tuple only, canonical-CBOR clean, no float ever  #
# near a hashed byte. The deterministic-check and window-closed signals are     #
# injected booleans so the IL-106/IL-107 producers plug in without touching     #
# this contract. It is purely additive: the symbols the uniform path exports    #
# (consumed by ``token.mint`` / ``pouw.escrow`` / ``pouw.marketplace``) are     #
# untouched.                                                                    #
# --------------------------------------------------------------------------- #

#: The existing deterministic re-execution / tolerance-digest policy (GPU and
#: synaptic-compile work that is byte-reproducible).
VERIFICATION_UNIFORM = "uniform"
#: The non-deterministic-work policy: deterministic structural re-check +
#: challenge-window settlement, reward withheld until both clear (IL-105).
VERIFICATION_SPLIT = "split"

_VERIFICATION_POLICIES = frozenset({VERIFICATION_UNIFORM, VERIFICATION_SPLIT})

#: Pipeline-stage tags (IL-105 AC4). The *work* is mining; the *verdict + reward*
#: is settlement. Plain string tags so they ride canonical CBOR unchanged.
STAGE_MINING = "mining"
STAGE_SETTLEMENT = "settlement"


@dataclass(frozen=True)
class JobClass:
    """A registered PoUW job class and the verification policy it settles under."""

    name: str
    verification: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("job class name must be a non-empty str")
        if self.verification not in _VERIFICATION_POLICIES:
            raise ValueError(
                f"unknown verification policy {self.verification!r} "
                f"(expected one of {sorted(_VERIFICATION_POLICIES)})"
            )


_JOB_CLASSES: dict[str, JobClass] = {}


def register_job_class(name: str, verification: str) -> JobClass:
    """Register (or re-confirm) a job class -> verification policy mapping.

    Idempotent for an identical (name, verification); raises on a conflicting
    re-registration so a policy can never be silently flipped under a live name.
    """
    candidate = JobClass(name=name, verification=verification)
    existing = _JOB_CLASSES.get(name)
    if existing is not None and existing != candidate:
        raise ValueError(
            f"job class {name!r} already registered as {existing.verification!r}; "
            f"refusing to redefine as {verification!r}"
        )
    _JOB_CLASSES[name] = candidate
    return candidate


def job_class(name: str) -> JobClass:
    """Look up a registered job class. Raises ``KeyError`` if absent."""
    return _JOB_CLASSES[name]


def verification_policy(name: str) -> str:
    """The verification policy string a registered job class settles under."""
    return _JOB_CLASSES[name].verification


# Built-in job classes: the legacy deterministic path stays UNIFORM; distill is
# the first SPLIT-verified class.
register_job_class("synaptic-compile", VERIFICATION_UNIFORM)
register_job_class("distill", VERIFICATION_SPLIT)


def bundle_cid(bytecode: bytes) -> str:
    """Content commitment for a compiled distill bundle.

    A canonical (dag-cbor sha2-256) CID over the bundle's content digest, so a
    manifest can name the exact output without carrying the raw bytecode. Pure
    str/bytes in, str CID out — no float, no wall-clock.
    """
    if not isinstance(bytecode, (bytes, bytearray)):
        raise TypeError("bytecode must be bytes")
    return canonical.cid({"kind": "distill-bundle", "digest": _bc.bundle_digest(bytes(bytecode))})


@dataclass(frozen=True)
class DistillManifest:
    """The ``distill`` job manifest (IL-105 AC2).

    Records exactly what binds a distillation job to its inputs and output:

    * ``query``         — a canonical CID *commitment* to the query (not the raw
                          query bytes: queries may carry non-canonical content, so
                          the manifest commits to a fingerprint, matching the
                          ``query_fingerprint`` convention distill already uses);
    * ``subscription``  — the subscription scope the candidates were drawn under
                          (``()`` when unscoped);
    * ``web_state_cid`` — the Web state root the retrieval/distillation ran over,
                          so re-execution (IL-106) is pinned to one graph;
    * ``bundle_cid``    — the content commitment of the distilled output bundle;
    * ``originator``    — the verified originator the bundle is attributed to.

    The manifest declares its ``job_class``/``verification``/``stage`` inline so a
    verifier reads the settlement policy off the manifest itself. ``to_record`` is
    canonical-CBOR clean (str/int/list only) and ``cid`` is its content address.
    """

    query: str
    subscription: tuple[str, ...]
    web_state_cid: str
    bundle_cid: str
    originator: str
    job_class: str = "distill"
    verification: str = VERIFICATION_SPLIT
    stage: str = STAGE_MINING

    def __post_init__(self) -> None:
        for name in ("query", "web_state_cid", "bundle_cid", "originator"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value:
                raise ValueError(f"DistillManifest.{name} must be a non-empty str")
        if not isinstance(self.subscription, tuple) or not all(
            isinstance(s, str) for s in self.subscription
        ):
            raise TypeError("subscription must be a tuple[str, ...]")
        if self.verification != VERIFICATION_SPLIT:
            raise ValueError("a distill manifest is split-verified by definition")
        if self.stage not in {STAGE_MINING, STAGE_SETTLEMENT}:
            raise ValueError(f"unknown stage {self.stage!r}")

    def to_record(self) -> dict:
        """Canonical-CBOR-friendly record (str/int/list only — no float, no None)."""
        return {
            "kind": "distill-manifest",
            "job_class": self.job_class,
            "verification": self.verification,
            "stage": self.stage,
            "query": self.query,
            "subscription": list(self.subscription),
            "web_state_cid": self.web_state_cid,
            "bundle_cid": self.bundle_cid,
            "originator": self.originator,
        }

    def cid(self) -> str:
        """Content address of this manifest."""
        return canonical.cid(self.to_record())

    @classmethod
    def from_distill(
        cls,
        candidate_set: object,
        *,
        bundle_cid: str,
        originator: str,
    ) -> "DistillManifest":
        """Derive a manifest from a ``retrieve.CandidateSet`` (duck-typed).

        Reads ``subscription``/``web_state_cid``/``query`` off the candidate set so
        ``pouw`` need not import the ``interpret`` layer. The query is committed as a
        canonical CID fingerprint rather than stored verbatim.
        """
        subscription = getattr(candidate_set, "subscription", None) or ()
        web_state_cid = getattr(candidate_set, "web_state_cid", None)
        raw_query = getattr(candidate_set, "query", None)
        if not isinstance(web_state_cid, str) or not web_state_cid:
            raise ValueError("candidate_set.web_state_cid must be a non-empty str")
        query_commitment = canonical.cid({"query": _stringify_query(raw_query)})
        return cls(
            query=query_commitment,
            subscription=tuple(subscription),
            web_state_cid=web_state_cid,
            bundle_cid=bundle_cid,
            originator=originator,
        )


def _stringify_query(query: object) -> object:
    """Canonical-safe projection of a query for fingerprinting (no float)."""
    if isinstance(query, dict):
        return {str(k): _stringify_query(v) for k, v in sorted(query.items(), key=lambda kv: str(kv[0]))}
    if isinstance(query, (list, tuple)):
        return [_stringify_query(v) for v in query]
    if isinstance(query, bool):
        return query
    if isinstance(query, int):
        return query
    if isinstance(query, float):
        # Canonical CBOR rejects floats; commit to a deterministic decimal string.
        return repr(query)
    return str(query)


@dataclass(frozen=True)
class SplitVerdict:
    """The settlement verdict for a split-verified (distill) job (IL-105 AC3/AC4).

    Bundles the three independent signals the split policy composes and the
    resulting reward eligibility. Tagged ``settlement`` — it is the verdict, not
    the mining work.
    """

    deterministic_ok: bool
    window_closed: bool
    dispute_upheld: bool
    stage: str = STAGE_SETTLEMENT

    @property
    def settles(self) -> bool:
        return split_settles(
            deterministic_ok=self.deterministic_ok,
            window_closed=self.window_closed,
            dispute_upheld=self.dispute_upheld,
        )


def split_settles(
    *, deterministic_ok: bool, window_closed: bool, dispute_upheld: bool
) -> bool:
    """Split-verification settlement policy (IL-105 AC3).

    Reward for a ``distill`` job settles **iff** all three hold:

    * ``deterministic_ok``  — the deterministic structural re-check passed
                              (IL-106: re-run retrieve + gate over the pinned
                              ``web_state_cid`` and confirm the gated structure);
    * ``window_closed``     — the challenge window has closed (IL-107 / the
                              existing ``pouw.dispute`` dispute window);
    * ``not dispute_upheld``— no dispute was upheld within that window.

    The three signals are produced elsewhere; this is the pure, side-effect-free
    composition so the policy can be tested and reasoned about independently of
    its (still-evolving) producers. Settlement is conjunctive and monotone: any
    single failing signal withholds the reward.
    """
    return bool(deterministic_ok) and bool(window_closed) and not bool(dispute_upheld)
