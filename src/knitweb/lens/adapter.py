"""Adapter — turn Knitweb primitives into virtualpc-agent-digestible Lens output.

This module bridges the credibly-neutral Knitweb core and lightweight virtualpc
LLM agents.  It ingests Pulse beats, Web weaves, and Fiber synaptic bundles,
projects them into a :class:`~knitweb.lens.space.LensSpace`, and renders a
deterministic context string (or message payload) that an agent can store in its
recursive memory or pass to an LLM.

The design is intentionally decoupled: Knitweb knows nothing about virtualpc's
message bus, and virtualpc needs only a string/dict from this adapter.
"""

from __future__ import annotations

from typing import Any, TypedDict

from ..core.pulse import Pulse, Beat
from ..fabric.web import Web, Edge
from ..synaptic.bytecode import decode_bundle, Relation
from ..synaptic.origintrail import resolve_asset
from .atom import Atom, SymbolAtom, ExpressionAtom, GroundedAtom
from .space import LensSpace
from .interpret import digest_context, interpret

__all__ = ["KnitwebLensAdapter", "DecodedBundle", "ResolvedAsset"]


class DecodedBundle(TypedDict):
    """Stable shape returned by :func:`knitweb.synaptic.bytecode.decode_bundle`."""

    asset_cid: str
    originator: str
    relations: list[Relation]


class ResolvedAsset(TypedDict):
    """Stable shape returned by :func:`knitweb.synaptic.origintrail.resolve_asset`."""

    asset_id: str
    originator: str
    relations: list[Relation]


class KnitwebLensAdapter:
    """Ingest Knitweb primitives and render LLM-digestible context.

    The adapter owns a :class:`LensSpace`.  Each ingestion adds atoms derived
    from the source primitive; repeated ingestion of the same content is
    idempotent because atoms are hashable and the space is a set.
    """

    def __init__(self, space: LensSpace | None = None) -> None:
        self.space = space or LensSpace()

    # -----------------------------------------------------------------------
    # Ingestion helpers
    # -----------------------------------------------------------------------

    def ingest_pulse(self, pulse: Pulse) -> None:
        """Add atoms for every beat in ``pulse``."""
        for beat in pulse.beats:
            self.space.add_all(_beat_to_atoms(beat))

    def ingest_web(self, web: Web) -> None:
        """Add atoms for every node and edge in ``web``.

        Uses public :meth:`Web.outgoing_edges` instead of the internal ``_out``
        attribute so the adapter stays decoupled from the Web implementation.
        """
        for cid, record in web.nodes.items():
            self.space.add_all(_node_to_atoms(cid, record))
            for edge in web.outgoing_edges(cid):
                self.space.add_all(_edge_to_atoms(edge))

    def ingest_bundle(self, bundle: bytes) -> None:
        """Decode a Fiber synaptic bytecode bundle and add its atoms."""
        decoded: DecodedBundle = decode_bundle(bundle)
        self.space.add_all(_bundle_to_atoms(decoded))

    def ingest_asset(self, asset: dict) -> None:
        """Resolve an OriginTrail-style Knowledge Asset and add its atoms."""
        asset_id, originator, relations = resolve_asset(asset)
        self.space.add_all(_relations_to_atoms(asset_id, originator, relations))

    # -----------------------------------------------------------------------
    # Output helpers
    # -----------------------------------------------------------------------

    def digest(
        self,
        focus: Atom | None = None,
        pattern: Atom | None = None,
        max_atoms: int = 64,
    ) -> str:
        """Render the current space as an LLM context string."""
        return digest_context(self.space, focus=focus, pattern=pattern, max_atoms=max_atoms)

    def to_message_payload(
        self,
        sender: str,
        topic: str,
        focus: Atom | None = None,
        pattern: Atom | None = None,
        max_atoms: int = 64,
    ) -> dict[str, Any]:
        """Return a dict payload compatible with virtualpc's message bus.

        virtualpc's ``MessageBus.publish`` accepts any JSON-serialisable
        payload.  This payload carries both the rendered digest and metadata
        so a receiving agent can route, store, or forward it.
        """
        return {
            "sender": sender,
            "topic": topic,
            "kind": "knitweb-lens-digest",
            "atom_count": len(self.space),
            "focus": interpret(focus) if focus else None,
            "query": interpret(pattern) if pattern else None,
            "content": self.digest(focus=focus, pattern=pattern, max_atoms=max_atoms),
        }


# ---------------------------------------------------------------------------
# Converters: Knitweb primitives -> atoms
# ---------------------------------------------------------------------------

def _beat_to_atoms(beat: Beat) -> list[Atom]:
    beat_sym = SymbolAtom("Beat")
    epoch = GroundedAtom(beat.epoch, "Integer")
    timestamp = GroundedAtom(beat.timestamp, "Integer")
    state_root = GroundedAtom(beat.state_root, "Hash")
    prev = GroundedAtom(beat.prev_beat, "CID") if beat.prev_beat else SymbolAtom("Genesis")
    return [
        ExpressionAtom(
            beat_sym,
            GroundedAtom(beat.cid, "CID"),
            ExpressionAtom(SymbolAtom("epoch"), epoch),
            ExpressionAtom(SymbolAtom("timestamp"), timestamp),
            ExpressionAtom(SymbolAtom("state-root"), state_root),
            ExpressionAtom(SymbolAtom("prev"), prev),
        ),
    ]


def _node_to_atoms(cid: str, record: dict) -> list[Atom]:
    node_sym = SymbolAtom("Node")
    return [
        ExpressionAtom(
            node_sym,
            GroundedAtom(cid, "CID"),
            GroundedAtom(record, "Record"),
        ),
    ]


def _edge_to_atoms(edge: Edge) -> list[Atom]:
    return [
        ExpressionAtom(
            SymbolAtom("Edge"),
            GroundedAtom(edge.src, "CID"),
            SymbolAtom(edge.rel),
            GroundedAtom(edge.dst, "CID"),
            GroundedAtom(edge.weight, "Integer"),
        ),
    ]


def _bundle_to_atoms(decoded: DecodedBundle) -> list[Atom]:
    return _relations_to_atoms(decoded["asset_cid"], decoded["originator"], decoded["relations"])


def _relations_to_atoms(asset_id: str, originator: str, relations: list[Relation]) -> list[Atom]:
    atoms: list[Atom] = [
        ExpressionAtom(
            SymbolAtom("Asset"),
            GroundedAtom(asset_id, "CID"),
            ExpressionAtom(SymbolAtom("originator"), GroundedAtom(originator, "String")),
        ),
    ]
    for rel in relations:
        atoms.append(
            ExpressionAtom(
                SymbolAtom("Relation"),
                GroundedAtom(asset_id, "CID"),
                ExpressionAtom(SymbolAtom("subject"), GroundedAtom(rel.subject, "String")),
                ExpressionAtom(SymbolAtom("predicate"), GroundedAtom(rel.predicate, "String")),
                ExpressionAtom(SymbolAtom("object"), GroundedAtom(rel.obj, "String")),
                ExpressionAtom(SymbolAtom("source-type"), SymbolAtom(rel.source_type)),
                ExpressionAtom(SymbolAtom("weight"), GroundedAtom(rel.weight, "Integer")),
            )
        )
    return atoms
