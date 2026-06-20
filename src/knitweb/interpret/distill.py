"""Recursive distillation over a candidate set.

This stage consumes ``CandidateSet`` without concatenating relation content into a
single prompt. It performs bounded, deterministic loops and emits a minimal signed
artifact candidate for downstream bytecode compilation.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable, Mapping

from ..fabric import attest
from ..fabric.web import Web
from ..fabric import provenance
from ..core import canonical
from ..synaptic import bytecode as _bc
from .quantize import quantize_weight
from .retrieve import CandidateSet

__all__ = [
    "DistillIterationLog",
    "Selection",
    "distill",
    "gate_relations",
]

_DISTILL_RELATION_KIND = "distill-relation"
_DISTILL_INTERMEDIATE_KIND = "distill-intermediate"
_DISTILLED_FROM_REL = "distilled-from"


def _require_int(name: str, value: int, minimum: int = 0) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{name} must be int")
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")


def _relation_key(relation: _bc.Relation) -> tuple:
    return (
        relation.subject,
        relation.predicate,
        relation.obj,
        relation.source_type,
        relation.weight,
    )


@dataclass(frozen=True)
class DistillIterationLog:
    """Per-run metrics from a bounded distill loop."""

    iterations: int
    sub_calls: int
    cache_hits: int
    elapsed_ms: int
    budget_exhausted: bool


@dataclass(frozen=True)
class Selection:
    """The distill output contract: selected relations + source coverage."""

    relations: tuple[_bc.Relation, ...]
    relation_sources: tuple[tuple[str, ...], ...]
    intermediate_cids: tuple[str, ...]
    log: DistillIterationLog
    query: str | object

    @property
    def relation_count(self) -> int:
        return len(self.relations)


def _query_fingerprint(query: str | object) -> str:
    if isinstance(query, Mapping):
        safe_query = _canonicalize_query_for_fingerprint(query)
        return canonical.cid(safe_query)
    return canonical.cid({"text": str(query)})


def _canonicalize_query_for_fingerprint(value: object) -> object:
    if isinstance(value, Mapping):
        normalized: dict[str, object] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("query keys must be str")
            normalized[key] = _canonicalize_query_for_fingerprint(item)
        return normalized
    if isinstance(value, list):
        return [_canonicalize_query_for_fingerprint(item) for item in value]
    if isinstance(value, tuple):
        return [_canonicalize_query_for_fingerprint(item) for item in value]
    if isinstance(value, set):
        ordered = [_canonicalize_query_for_fingerprint(item) for item in value]
        return sorted(ordered, key=repr)
    if isinstance(value, float):
        # Canonical CBOR used by this codebase rejects floats. Keep digest stability
        # without dropping signal by preserving a deterministic decimal string.
        return repr(value)
    return value


def _relation_record(relation: _bc.Relation) -> dict:
    """Deterministic node payload for a distilled relation."""
    return {
        "kind": _DISTILL_RELATION_KIND,
        "subject": relation.subject,
        "predicate": relation.predicate,
        "object": relation.obj,
        "source_type": relation.source_type,
        "weight": relation.weight,
    }


def _parse_relation_record(record: dict) -> _bc.Relation:
    if not isinstance(record, dict) or record.get("kind") != _DISTILL_RELATION_KIND:
        raise TypeError("relation node missing distill payload")
    try:
        return _bc.Relation(
            subject=record["subject"],
            predicate=record["predicate"],
            obj=record["object"],
            source_type=str(record.get("source_type", "Unknown")),
            weight=int(record["weight"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise TypeError("malformed distill relation node") from exc


def _intermediate_record(
    query: str | object,
    candidate_cid: str,
    relation_cid: str,
    mode: str,
) -> dict:
    return {
        "kind": _DISTILL_INTERMEDIATE_KIND,
        "query_fingerprint": _query_fingerprint(query),
        "candidate": candidate_cid,
        "relation": relation_cid,
        "mode": mode,
    }


def _relation_signature(
    candidate_cid: str, relation: _bc.Relation, mode: str, query: str | object
) -> tuple[str, str]:
    """Stable tuple for distill intermediate/relation reuse."""
    relation_record = _relation_record(relation)
    relation_cid = canonical.cid(relation_record)
    intermediate_record = _intermediate_record(query, candidate_cid, relation_cid, mode)
    return relation_cid, canonical.cid(intermediate_record)


def _gate_relation(
    relation: _bc.Relation,
    web: Web,
) -> bool:
    """Deterministic attestation gate for a relation.

    Fabricated nodes are never emitted. We gate on ``attested`` graph membership for
    subject/predicate/object CIDs and on acyclic provenance.
    """
    if not all(isinstance(x, str) and x for x in (relation.subject, relation.predicate, relation.obj)):
        return False
    if relation.subject not in web.nodes or relation.predicate not in web.nodes or relation.obj not in web.nodes:
        return False

    # Re-check cycle safety so distill never emits cyclic provenance claims.
    if not provenance.is_acyclic(web, relation.subject):
        return False
    if not provenance.is_acyclic(web, relation.predicate):
        return False
    if not provenance.is_acyclic(web, relation.obj):
        return False

    # Reuse the attestation surface when available. If no explicit attestation is
    # attached (legacy records), this becomes a graph-membership + acyclicity gate.
    return (
        attest.node_is_attested(web, relation.subject)
        and attest.node_is_attested(web, relation.predicate)
        and attest.node_is_attested(web, relation.obj)
    )


def gate_relations(
    relations: Iterable[_bc.Relation],
    candidates: CandidateSet,
    web: Web,
) -> tuple[_bc.Relation, ...]:
    """Apply deterministic gate checks to a relation stream and drop fabricated tuples."""
    out: list[_bc.Relation] = []
    for relation in relations:
        if _gate_relation(relation, web):
            out.append(relation)
    return tuple(out)


def _relation_from_candidate(
    candidate_cid: str,
    web: Web,
    *,
    query: str | object,
    reputation: int,
    recency: float = 1.0,
) -> _bc.Relation:
    subject = candidate_cid
    obj = candidate_cid
    neighbors = []
    if web is not None:
        neighbors = web.neighbors(candidate_cid)
    predicate = neighbors[0] if neighbors else candidate_cid
    source_type = "Unknown"

    if isinstance(query, dict):
        if isinstance(query.get("subject"), str):
            qs = str(query["subject"])  # type: ignore[index]
            subject = qs if qs in web.nodes else candidate_cid
        if isinstance(query.get("predicate"), str):
            qp = str(query["predicate"])  # type: ignore[index]
            predicate = qp if qp in web.nodes else predicate
        if isinstance(query.get("object"), str):
            qo = str(query["object"])  # type: ignore[index]
            obj = qo if qo in web.nodes else candidate_cid
        if isinstance(query.get("source_type"), str):
            source_type = str(query["source_type"])  # type: ignore[index]

    recency_value = recency
    pouw_score = 1.0
    if isinstance(query, dict) and isinstance(query.get("pouw_score"), (int, float)):
        pouw_score = float(query["pouw_score"])  # type: ignore[index]
    elif isinstance(query, dict) and isinstance(query.get("weight"), (int, float)):
        # Compatibility shim for callers that send legacy output weight hints.
        pouw_score = float(query["weight"])  # type: ignore[index]
    if isinstance(query, dict) and isinstance(query.get("recency"), (int, float)):
        recency_value = float(query["recency"])  # type: ignore[index]

    weight = quantize_weight(
        reputation=reputation,
        recency=recency_value,
        pouw_score=pouw_score,
    )

    return _bc.Relation(
        subject=subject,
        predicate=predicate,
        obj=obj,
        source_type=source_type,
        weight=weight,
    )


def _emit_intermediate(
    web: Web,
    query: str | object,
    candidate: str,
    relation: _bc.Relation,
    mode: str,
    *, is_cached: bool,
) -> tuple[str, str]:
    relation_record = _relation_record(relation)
    relation_cid = canonical.cid(relation_record)
    if not is_cached:
        web.weave(relation_record)
    intermediate_record = _intermediate_record(
        query=query,
        candidate_cid=candidate,
        relation_cid=relation_cid,
        mode=mode,
    )
    intermediate_cid = canonical.cid(intermediate_record)
    web.weave(intermediate_record)
    try:
        web.link(intermediate_cid, relation_cid, _DISTILLED_FROM_REL, weight=1)
    except (KeyError, ValueError):
        # `web.link` can fail deterministically if node ordering changes or an
        # internal relation is stale; this keeps distillation moving and leaves
        # a deterministic fallback for caller-facing outputs.
        pass
    return relation_cid, intermediate_cid


def distill(
    candidates: CandidateSet,
    query: str | object,
    *,
    max_iters: int = 8,
    mode: str = "reflect",
    web: Web,
    max_prompt_bytes: int = 8 * 1024,
) -> Selection:
    """Select relations from a deterministic candidate frontier.

    The loop is strictly bounded by ``max_iters`` and emits relation-level metrics
    so callers can enforce mining budgets.
    """
    _require_int("max_iters", max_iters, minimum=1)
    if mode not in {"reflect", "recurse"}:
        raise ValueError("mode must be 'reflect' or 'recurse'")

    start = time.monotonic_ns()
    if mode == "recurse":
        # Keep recurse deterministic and bounded by explicit budget.
        max_iters = max(2, max_iters * 2)

    budget_exhausted = len(candidates.cids) > max_iters
    iters = min(len(candidates.cids), max_iters)
    candidate_reputation = {candidate.cid: candidate.reputation for candidate in candidates.candidates}

    collected: dict[tuple, _bc.Relation] = {}
    source_map: dict[tuple, tuple[str, ...]] = {}
    sub_calls = 0
    prompt_bytes = 0
    cache_hits = 0
    intermediate_order: list[str] = []

    for candidate in candidates.cids[:iters]:
        candidate_meta = candidate_reputation.get(candidate, 0)
        rel = _relation_from_candidate(
            candidate,
            web,
            query=query,
            recency=1.0,
            reputation=candidate_meta,
        )
        relation_record = _relation_record(rel)
        rel_cid, inter_cid = _relation_signature(
            candidate, rel, mode, query
        )
        is_cached = rel_cid in web.nodes and inter_cid in web.nodes
        if is_cached:
            try:
                rel_record = web.get(rel_cid)
                rel = _parse_relation_record(rel_record) if rel_record is not None else rel
            except TypeError:
                # Corrupt cache entries are ignored and rebuilt.
                rel = _relation_from_candidate(
                    candidate,
                    web,
                    query=query,
                    recency=1.0,
                    reputation=candidate_meta,
                )
                is_cached = False

        if not is_cached:
            web.weave(relation_record)
            rel_cid, inter_cid = _emit_intermediate(
                web=web,
                query=query,
                candidate=candidate,
                relation=rel,
                mode=mode,
                is_cached=False,
            )
        else:
            cache_hits += 1
            rel_cid, inter_cid = _emit_intermediate(
                web=web,
                query=query,
                candidate=candidate,
                relation=rel,
                mode=mode,
                is_cached=True,
            )

        intermediate_order.append(inter_cid)
        rel_key = _relation_key(rel)
        if rel_key in collected:
            # stable dedupe; preserve source union for reproducibility
            source_map[rel_key] += (candidate,)
            continue

        sub_calls += 1
        rel_bytes = (len(rel.subject) + len(rel.predicate) + len(rel.obj)).to_bytes(8, "big")
        prompt_bytes += len(rel_bytes)
        if prompt_bytes > max_prompt_bytes:
            break

        if _gate_relation(rel, web):
            collected[rel_key] = rel
            source_map[rel_key] = (candidate,)

    elapsed_ms = max(0, time.monotonic_ns() - start) // 1_000_000
    relations = tuple(collected.values())
    return Selection(
        relations=relations,
        relation_sources=tuple(source_map.get(_relation_key(r), ()) for r in relations),
        intermediate_cids=tuple(intermediate_order),
        log=DistillIterationLog(
            iterations=iters,
            sub_calls=sub_calls,
            cache_hits=cache_hits,
            elapsed_ms=elapsed_ms,
            budget_exhausted=budget_exhausted,
        ),
        query=query,
    )
