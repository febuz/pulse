"""Content-addressed agent-memory node kinds.

The substrate primitive for the lens "content-addressed agent memory" epic: four
node kinds — :class:`SkillNode`, :class:`ProjectNode`, :class:`PreferenceNode` and
:class:`ArchitectureNode` — that encode through the existing canonical CIDv1
dag-cbor path (:func:`knitweb.core.canonical.cid`). Identical agent knowledge
therefore collapses to a single CID across repositories and tools.

Design contract
---------------
* **Content-only.** A memory record carries *only* its semantic content. It holds
  no author, file path, repository, or timestamp, so the *same* skill or
  preference authored in two different repos addresses to the *same* CID. (Edge
  semantics, provenance and orchestration land in the lens layer, not here.)
* **Normalized for dedup.** Text fields are stripped of surrounding whitespace and
  list fields (``tags``, ``components``) are sorted and de-duplicated, so two tool
  files holding the same knowledge — modulo incidental whitespace or ordering —
  resolve to one node CID.
* **Integer-only / float-free.** Records pass through ``canonical.encode``, which
  rejects floats and non-minimal integers; ``to_record`` fails fast on any
  non-canonical content.
* **Versioning.** Each kind's ``KIND`` string *is* its schema version. Additive
  fields use conditional omission (an empty optional field is omitted, preserving
  the byte-identical CID of records that do not set it); a breaking change bumps
  the ``KIND`` string. This mirrors the ``reaction-knowledge`` migration policy.

This module defines schema + canonical addressing only. It adds no orchestration,
no adapters, and no Knit-edge semantics.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import canonical

__all__ = [
    "MemoryNode",
    "SkillNode",
    "ProjectNode",
    "PreferenceNode",
    "ArchitectureNode",
    "MEMORY_NODE_KINDS",
    "node_cid",
    "node_from_record",
]


def _norm_text(name: str, value: str, *, required: bool = True) -> str:
    """Strip surrounding whitespace; reject non-str and (when required) empty text."""
    if not isinstance(value, str):
        raise TypeError(f"{name} must be str")
    stripped = value.strip()
    if required and not stripped:
        raise ValueError(f"{name} must be non-empty")
    return stripped


def _norm_labels(name: str, values: "tuple[str, ...] | list[str]") -> tuple[str, ...]:
    """Sort + de-duplicate a label list; reject non-str members and drop blanks."""
    out: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{name} members must be str")
        stripped = value.strip()
        if stripped:
            out.add(stripped)
    return tuple(sorted(out))


class MemoryNode:
    """Base contract for a content-addressed agent-memory node.

    Subclasses are frozen dataclasses that set a distinct ``KIND`` and implement
    :meth:`_content`, returning the normalized semantic fields. :meth:`to_record`
    frames them with ``kind`` and the normalized ``tags``, then validates
    canonical-encodability.
    """

    KIND = "agent-memory"
    tags: tuple[str, ...] = ()

    def _content(self) -> dict:  # pragma: no cover - overridden by every subclass
        raise NotImplementedError

    def to_record(self) -> dict:
        """Build the integer-/str-only canonical record for this node."""
        record: dict = {"kind": self.KIND}
        record.update(self._content())
        labels = _norm_labels("tags", self.tags)
        if labels:  # conditional omission keeps tagless records byte-stable
            record["tags"] = list(labels)
        # Fail fast on any non-canonical content (floats, non-str keys, ...).
        canonical.encode(record)
        return record

    @property
    def cid(self) -> str:
        """The CIDv1 (dag-cbor / sha2-256) address of this node's canonical record."""
        return canonical.cid(self.to_record())


@dataclass(frozen=True)
class SkillNode(MemoryNode):
    """A reusable skill or capability the agent has learned."""

    KIND = "agent-skill"

    name: str
    body: str
    tags: tuple[str, ...] = ()

    def _content(self) -> dict:
        return {
            "name": _norm_text("name", self.name),
            "body": _norm_text("body", self.body),
        }


@dataclass(frozen=True)
class ProjectNode(MemoryNode):
    """An ongoing project, goal, or workstream."""

    KIND = "agent-project"

    name: str
    goal: str
    status: str = ""
    tags: tuple[str, ...] = ()

    def _content(self) -> dict:
        content = {
            "name": _norm_text("name", self.name),
            "goal": _norm_text("goal", self.goal),
        }
        status = _norm_text("status", self.status, required=False)
        if status:  # optional; omitted when unset to keep the CID byte-stable
            content["status"] = status
        return content


@dataclass(frozen=True)
class PreferenceNode(MemoryNode):
    """A durable user or agent preference (how work should be done)."""

    KIND = "agent-preference"

    scope: str
    statement: str
    tags: tuple[str, ...] = ()

    def _content(self) -> dict:
        return {
            "scope": _norm_text("scope", self.scope),
            "statement": _norm_text("statement", self.statement),
        }


@dataclass(frozen=True)
class ArchitectureNode(MemoryNode):
    """An architecture decision or structural fact about a system."""

    KIND = "agent-architecture"

    name: str
    decision: str
    components: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()

    def _content(self) -> dict:
        content = {
            "name": _norm_text("name", self.name),
            "decision": _norm_text("decision", self.decision),
        }
        parts = _norm_labels("components", self.components)
        if parts:  # optional; omitted when empty
            content["components"] = list(parts)
        return content


#: Registry mapping each node kind's ``KIND`` string to its class.
MEMORY_NODE_KINDS: dict = {
    cls.KIND: cls
    for cls in (SkillNode, ProjectNode, PreferenceNode, ArchitectureNode)
}


def node_cid(node: MemoryNode) -> str:
    """Content-address ``node`` (convenience wrapper over :attr:`MemoryNode.cid`)."""
    return node.cid


def node_from_record(record: dict) -> MemoryNode:
    """Reconstruct a memory node from its canonical record (round-trip inverse).

    The reconstructed node re-addresses to the same CID as the originating node,
    so a record that round-trips through ``canonical.encode``/``decode`` and back
    through this function preserves byte-identity.
    """
    if not isinstance(record, dict):
        raise TypeError("record must be a dict")
    kind = record.get("kind")
    cls = MEMORY_NODE_KINDS.get(kind)
    if cls is None:
        raise ValueError(f"unknown agent-memory kind: {kind!r}")
    tags = tuple(record.get("tags", ()))
    if cls is SkillNode:
        return SkillNode(name=record["name"], body=record["body"], tags=tags)
    if cls is ProjectNode:
        return ProjectNode(
            name=record["name"],
            goal=record["goal"],
            status=record.get("status", ""),
            tags=tags,
        )
    if cls is PreferenceNode:
        return PreferenceNode(
            scope=record["scope"], statement=record["statement"], tags=tags
        )
    return ArchitectureNode(
        name=record["name"],
        decision=record["decision"],
        components=tuple(record.get("components", ())),
        tags=tags,
    )
