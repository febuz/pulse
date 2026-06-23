"""LensSpace — a lightweight atomspace for Knitweb.

A dependency-free translation of the Hyperon / MeTTa space idea
(see `trueagi-io/hyperon-experimental`).  A space is a bag of atoms that
supports pattern matching with variables.  It is intentionally tiny: no
unification engine, no interpreter, just structural matching over atoms.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .atom import Atom, ExpressionAtom, SymbolAtom, VariableAtom

__all__ = ["LensSpace", "Binding"]


@dataclass(frozen=True)
class Binding:
    """A variable binding produced by pattern matching.

    The mapping is exposed as a read-only :class:`~collections.abc.Mapping` and
    copied on construction so the frozen binding cannot be mutated through the
    underlying dict.
    """

    mapping: Mapping[str, Atom]

    def __post_init__(self) -> None:
        object.__setattr__(self, "mapping", dict(self.mapping))

    def get(self, name: str) -> Atom | None:
        return self.mapping.get(name)

    def merge(self, other: "Binding") -> "Binding | None":
        """Merge two bindings, returning ``None`` if they conflict."""
        merged = dict(self.mapping)
        for name, atom in other.mapping.items():
            if name in merged and merged[name] != atom:
                return None
            merged[name] = atom
        return Binding(merged)


class LensSpace:
    """A content-addressable-ish space of atoms with pattern matching.

    Adding the same atom twice is idempotent.  Queries use simple structural
    matching: :class:`VariableAtom` instances in the pattern bind to any atom,
    and nested expressions are matched recursively.
    """

    def __init__(self) -> None:
        self._atoms: set[Atom] = set()

    def add(self, atom: Atom) -> None:
        if not isinstance(atom, Atom):
            raise TypeError("LensSpace.add expects an Atom")
        self._atoms.add(atom)

    def add_all(self, atoms: Iterable[Atom]) -> None:
        for atom in atoms:
            self.add(atom)

    def remove(self, atom: Atom) -> bool:
        """Remove an atom.  Returns ``True`` iff it was present."""
        if atom in self._atoms:
            self._atoms.discard(atom)
            return True
        return False

    def __contains__(self, atom: Atom) -> bool:
        return atom in self._atoms

    def __len__(self) -> int:
        return len(self._atoms)

    def atoms(self) -> list[Atom]:
        """Return a deterministic list of all atoms."""
        return sorted(self._atoms, key=_atom_sort_key)

    def query(self, pattern: Atom) -> list[tuple[Atom, Binding]]:
        """Match ``pattern`` against every atom in the space.

        Returns a list of ``(matched_atom, binding)`` pairs.  Variables in the
        pattern bind to concrete atoms in the stored atom; the same variable
        name must bind to the same atom consistently within a single match.
        """
        results: list[tuple[Atom, Binding]] = []
        for atom in self._atoms:
            binding = _match(pattern, atom, Binding({}))
            if binding is not None:
                results.append((atom, binding))
        # Deterministic order for reproducible LLM context.
        results.sort(key=lambda pair: _atom_sort_key(pair[0]))
        return results

    def query_symbol(self, name: str) -> list[Atom]:
        """Return all atoms that are exactly the named symbol."""
        target = SymbolAtom(name)
        return sorted(
            (atom for atom in self._atoms if atom == target),
            key=_atom_sort_key,
        )


def _atom_sort_key(atom: Atom) -> str:
    """Stable textual key for deterministic ordering."""
    return str(atom)


def _match(pattern: Atom, target: Atom, binding: Binding) -> Binding | None:
    """Recursively match ``pattern`` against ``target``.

    Returns an updated binding, or ``None`` if they do not match.
    """
    if isinstance(pattern, VariableAtom):
        existing = binding.get(pattern.name)
        if existing is None:
            return Binding(dict(binding.mapping, **{pattern.name: target}))
        return binding if existing == target else None

    if type(pattern) is not type(target):
        return None

    if isinstance(pattern, ExpressionAtom):
        target_expr = target
        if len(pattern.children) != len(target_expr.children):
            return None
        current = binding
        for pc, tc in zip(pattern.children, target_expr.children):
            current = _match(pc, tc, current)
            if current is None:
                return None
        return current

    # SymbolAtom and GroundedAtom use value equality.
    return binding if pattern == target else None
