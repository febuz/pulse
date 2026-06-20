"""Deterministic candidate retrieval over a woven web.

This module implements a read-path first stage:

* run a constrained graph walk from query-derived seeds using ``Web.traverse`` and
  ``Web.neighbors``
* optionally union with a spatial hit set from ``SpatialIndex.near``
* attach cheap provenance roots via ``provenance.ancestry`` for deterministic ranking

No model logic is run here; only web-native traversal and deterministic ordering.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from ..fabric import provenance
from ..fabric.items import web_state_root
from ..fabric.spatial_index import SpatialIndex
from ..fabric.web import Web

__all__ = ["CandidateSet", "Candidate", "retrieve"]


def _require_iterable(name: str, value: Iterable[str] | None) -> tuple[str, ...] | None:
    if value is None:
        return None
    if isinstance(value, str):
        raise TypeError(f"{name} must be an iterable of scope names, not str")
    out: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item:
            raise TypeError(f"{name} entries must be non-empty str")
        out.append(item)
    return tuple(out)


def _in_scope(record: dict, scope: tuple[str, ...] | None) -> bool:
    if scope is None:
        return True
    values = {record.get("kind"), record.get("scope"), record.get("domain"), record.get("namespace")}
    if any(v in scope for v in values if isinstance(v, str)):
        return True
    tags = record.get("tags")
    if isinstance(tags, (list, tuple, set)):
        if any(str(tag) in scope for tag in tags):
            return True
    return False


def _to_query_dict(query: str | Mapping[str, object]) -> Mapping[str, object]:
    if isinstance(query, Mapping):
        return query
    if not isinstance(query, str):
        raise TypeError("query must be str or mapping")
    if not query:
        raise ValueError("query must not be empty")
    return {"text": query}


def _query_seeds(query: Mapping[str, object], web: Web) -> tuple[str, ...]:
    if "seed" in query:
        seeds = query["seed"]
        if isinstance(seeds, str):
            return (seeds,)
        if not isinstance(seeds, (list, tuple, set)):
            raise TypeError("query['seed'] must be str, list, tuple, or set")
        out: list[str] = []
        for sid in seeds:
            if not isinstance(sid, str) or not sid:
                raise TypeError("seed values must be non-empty str")
            out.append(sid)
        return tuple(out)

    text = query.get("text")
    if isinstance(text, str) and text in web.nodes:
        return (text,)

    kinds: tuple[str, ...] = ()
    if "kind" in query:
        k = query["kind"]
        kinds = (str(k),) if isinstance(k, str) else tuple(str(i) for i in k) if isinstance(k, Iterable) else ()
    out = []
    for cid, record in sorted(web.nodes.items(), key=lambda kv: kv[0]):
        if kinds and str(record.get("kind", "")) not in kinds:
            continue
        if "text" in query and isinstance(text, str):
            haystack = " ".join(str(v) for v in record.values())
            if str(text).lower() not in haystack.lower():
                continue
        out.append(cid)
    return tuple(out)


def _query_rels(query: Mapping[str, object]) -> set[str] | None:
    rels = query.get("rel")
    if rels is None:
        return None
    if isinstance(rels, str):
        return {str(rels)}
    if not isinstance(rels, Iterable):
        raise TypeError("query['rel'] must be str or iterable of str")
    out: set[str] = set()
    for rel in rels:
        if not isinstance(rel, str) or not rel:
            raise TypeError("query['rel'] entries must be non-empty str")
        out.add(rel)
    return out


@dataclass(frozen=True)
class Candidate:
    """One minimal relation candidate selected by ``retrieve``."""

    cid: str
    source_cids: tuple[str, ...]
    reputation: int = 0


@dataclass(frozen=True)
class CandidateSet:
    """Deterministic candidate slice returned by ``retrieve``."""

    query: str | Mapping[str, object]
    subscription: tuple[str, ...] | None
    web_state_cid: str
    cids: tuple[str, ...]
    candidates: tuple[Candidate, ...]
    source_ancestries: tuple[tuple[str, ...], ...]

    def records(self, web: Web) -> dict[str, dict]:
        """Return the full records for all candidate CIDs (lazy pull by ``web.get``)."""
        return {cid: web.get(cid) for cid in self.cids if web.get(cid) is not None}


def retrieve(
    query: str | Mapping[str, object],
    subscription: Iterable[str] | None,
    web: Web,
    *,
    depth: int = 2,
    web_state_cid: str | None = None,
    spatial_index: SpatialIndex | None = None,
) -> CandidateSet:
    """Return a deterministic, subscription-gated candidate set from ``web``.

    Parameters
    ----------
    query
        A query string (seed CID or free text) or structured query mapping.
    subscription
        One or more scope strings (``kind``/``scope`` style domains).
    web
        The shared web to query.
    web_state_cid
        Optional expected web state root. If supplied and mismatched, the query fails
        fast so a stale snapshot cannot silently cross web epochs.
    spatial_index
        Optional spatial index to union with graph traversal.
    """
    if depth < 0:
        raise ValueError("depth must be >= 0")

    if web_state_cid is not None and web_state_root(web) != web_state_cid:
        raise ValueError("web_state_cid mismatch")

    scope = _require_iterable("subscription", subscription)
    q = _to_query_dict(query)
    rel_filter = _query_rels(q)

    seed_cids = _query_seeds(q, web)
    if not seed_cids:
        current = web_state_cid or web_state_root(web)
        return CandidateSet(query=q, subscription=scope, web_state_cid=current,
                           cids=(), candidates=(), source_ancestries=())

    discovered: list[str] = []
    for sid in seed_cids:
        if sid in web.nodes and sid not in discovered:
            discovered.append(sid)
        for c in sorted(web.traverse(sid, depth=depth, rels=rel_filter)):
            if c not in discovered:
                discovered.append(c)

    if spatial_index is not None and "geohash" in q and "precision" in q:
        precision = q.get("precision")
        if not isinstance(precision, int):
            raise TypeError("query['precision'] must be int when spatial query is used")
        geohash = str(q["geohash"])
        near = spatial_index.near(geohash, precision, alt_band=q.get("alt_band"))  # type: ignore[arg-type]
        for cid in near:
            if cid not in discovered:
                discovered.append(cid)

    scoped = [cid for cid in discovered if _in_scope(web.nodes[cid], scope)]

    def _candidate_reputation(cid: str) -> int:
        score = 0
        for edge in web.outgoing_edges(cid):
            rep = web.edge_metadata(edge).get("reputation")
            if isinstance(rep, int) and rep > score:
                score = rep
        for edge in web.incoming_edges(cid):
            rep = web.edge_metadata(edge).get("reputation")
            if isinstance(rep, int) and rep > score:
                score = rep
        return score

    scored = [(cid, _candidate_reputation(cid)) for cid in scoped]
    ordered = sorted(scored, key=lambda item: (-item[1], item[0]))
    cids = tuple(cid for cid, _ in ordered)
    score_by_cid = {cid: score for cid, score in ordered}

    candidate_records: list[Candidate] = []
    ancestor_records: list[tuple[str, ...]] = []
    for cid in cids:
        try:
            ancestors = tuple(provenance.ancestry(web, cid))
        except Exception:
            ancestors = ()
        candidate_records.append(
            Candidate(cid=cid, source_cids=ancestors[:1], reputation=score_by_cid.get(cid, 0))
        )  # minimal source signature + rank score
        ancestor_records.append(ancestors)

    current = web_state_cid or web_state_root(web)
    return CandidateSet(
        query=q,
        subscription=scope,
        web_state_cid=current,
        cids=cids,
        candidates=tuple(candidate_records),
        source_ancestries=tuple(ancestor_records),
    )
