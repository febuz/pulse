"""OriginTrail resolver — turn a verified Knowledge Asset into Fiber relations.

The symbiosis: **OriginTrail** is the ground-truth/provenance layer (a Decentralised
Knowledge Graph that records *who* originated data — IFRS filings, news, image and
video libraries incl. YouTube/Youku/RuTube — and proves it). **Fiber** is the
execution layer: it reads those verified assets, extracts the relation matrix, and
compiles it to synaptic bytecode for edge AI / AR.

This module parses an OriginTrail-style Knowledge Asset (JSON-LD-ish dict) into a
list of :class:`~knitweb.synaptic.bytecode.Relation`. It accepts two shapes,
giving the input the benefit of the doubt:

  1. **Explicit triples** — an ``@graph`` / ``assertion`` list of
     ``{subject, predicate, object, (type)}`` items.
  2. **Linked sources** — an ``originator`` plus ``linked_sources`` list of
     ``{type, url}``; each becomes ``(asset, hasSource:<type>, url)``.

It never invents data: only fields present in the asset are emitted.
"""

from __future__ import annotations

from .bytecode import Relation, SOURCE_TYPES

__all__ = ["resolve_asset"]


def _asset_id(asset: dict) -> str:
    for key in ("origintrail_id", "@id", "id", "ual"):
        if key in asset and asset[key] is not None:
            return str(asset[key])
    return "unknown-asset"


def _normalize_source_type(value: str | None) -> str:
    if not value:
        return "Unknown"
    return value if value in SOURCE_TYPES else "Unknown"


def resolve_asset(asset: dict) -> tuple[str, str, list[Relation]]:
    """Return (asset_id, originator, relations) extracted from a Knowledge Asset."""
    asset_id = _asset_id(asset)
    originator = str(asset.get("originator", "Unknown"))
    relations: list[Relation] = []

    # Shape 1: explicit triples.
    triples = asset.get("@graph") or asset.get("assertion") or []
    if isinstance(triples, list):
        for t in triples:
            if not isinstance(t, dict):
                continue
            subj = t.get("subject") or t.get("@id")
            pred = t.get("predicate")
            obj = t.get("object") or t.get("@value")
            if subj is None or pred is None or obj is None:
                continue
            relations.append(
                Relation(
                    subject=str(subj),
                    predicate=str(pred),
                    obj=str(obj),
                    source_type=_normalize_source_type(t.get("type")),
                    weight=int(t.get("weight", 1)),
                )
            )

    # Shape 2: linked sources.
    for src in asset.get("linked_sources", []) or []:
        if not isinstance(src, dict):
            continue
        url = src.get("url")
        if url is None:
            continue
        src_type = _normalize_source_type(src.get("type"))
        relations.append(
            Relation(
                subject=asset_id,
                predicate=f"hasSource:{src_type}",
                obj=str(url),
                source_type=src_type,
                weight=int(src.get("weight", 1)),
            )
        )

    return asset_id, originator, relations
