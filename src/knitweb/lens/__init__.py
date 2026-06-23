"""Lens — a MeTTa-inspired view over Knitweb state for LLM digestion.

A Lens turns Knitweb primitives (Pulse beats, Web weaves, synaptic bundles)
into atoms in a lightweight hypergraph space, then interprets those atoms into
compact, LLM-readable context.  It is inspired by OpenCog Hyperon / MeTTa
(`trueagi-io/hyperon-experimental`): programs and data live in a space of
atoms, pattern matching selects relevant subgraphs, and grounded atoms carry
native Python values.

The implementation is intentionally dependency-free and minimal — a pure-Python
translation of the Hyperon atom/space/interpret pattern, scoped to what
virtualpc LLM agents need to digest the fabric.
"""

from __future__ import annotations

from .atom import Atom, SymbolAtom, ExpressionAtom, VariableAtom, GroundedAtom
from .space import LensSpace, Binding
from .interpret import interpret, digest_context
from .adapter import KnitwebLensAdapter

__all__ = [
    "Atom",
    "SymbolAtom",
    "ExpressionAtom",
    "VariableAtom",
    "GroundedAtom",
    "LensSpace",
    "Binding",
    "interpret",
    "digest_context",
    "KnitwebLensAdapter",
]
