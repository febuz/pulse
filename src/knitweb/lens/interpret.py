"""Interpret Lens atoms into LLM-digestible text.

A MeTTa program is a metagraph rewrite: atoms are data, and evaluation turns
atoms into other atoms.  For Knitweb's LLM agents the useful "interpretation"
is a controlled projection from the atomspace to a compact context string:
select relevant atoms, expand grounded values, and render them in natural
language the agent can digest.
"""

from __future__ import annotations

from .atom import Atom, SymbolAtom, ExpressionAtom, VariableAtom, GroundedAtom
from .space import LensSpace, Binding

__all__ = ["interpret", "digest_context"]


def interpret(atom: Atom, binding: Binding | None = None) -> str:
    """Render a single atom as text.

    Grounded atoms are rendered via their stored representation.  Expressions
    are rendered as ``(head arg1 arg2 ...)``.  Symbols render as their name.
    """
    if isinstance(atom, SymbolAtom):
        return atom.name
    if isinstance(atom, VariableAtom):
        bound = binding.get(atom.name) if binding else None
        return interpret(bound) if bound else str(atom)
    if isinstance(atom, GroundedAtom):
        return atom.render
    if isinstance(atom, ExpressionAtom):
        inner = " ".join(interpret(child, binding) for child in atom.children)
        return f"({inner})"
    return str(atom)


def digest_context(
    space: LensSpace,
    focus: Atom | None = None,
    pattern: Atom | None = None,
    max_atoms: int = 64,
) -> str:
    """Build a compact, deterministic context string from a LensSpace.

    Parameters
    ----------
    focus:
        Optional atom to centre the digest on.  If provided, it is rendered
        first and treated as the topic.
    pattern:
        Optional query pattern.  Only matching atoms are included.  If omitted,
        all atoms are considered.
    max_atoms:
        Hard cap on the number of atoms rendered, to keep the context window
        small for edge and LLM consumption.

    Returns
    -------
    A string suitable for feeding into a virtualpc agent's
    :meth:`RecursiveMemory.observe` or an LLM prompt context block.
    """
    if pattern is not None:
        matches = space.query(pattern)
        atoms = [atom for atom, _binding in matches]
    else:
        atoms = space.atoms()

    # Deduplicate while preserving order (Atom is hashable/equatable).
    seen: set[Atom] = set()
    unique: list[Atom] = []
    for atom in atoms:
        if atom not in seen:
            seen.add(atom)
            unique.append(atom)

    if focus is not None and focus in space:
        # Move focus to the front if it exists in the space.
        try:
            unique.remove(focus)
        except ValueError:
            pass
        unique.insert(0, focus)

    selected = unique[:max_atoms]

    lines = ["# Knitweb Lens digest"]
    if focus is not None:
        lines.append(f"Focus: {interpret(focus)}")
    if pattern is not None:
        lines.append(f"Query: {interpret(pattern)}")
    lines.append(f"Atoms: {len(selected)}")
    lines.append("")

    for atom in selected:
        rendered = interpret(atom)
        # Keep lines compact for LLM context windows.
        if len(rendered) > 400:
            rendered = rendered[:397] + "..."
        lines.append(f"- {rendered}")

    return "\n".join(lines)
