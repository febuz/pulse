"""Atoms — the primitive units of a Knitweb Lens.

This is a dependency-free translation of the MeTTa / Hyperon atom model
(see `trueagi-io/hyperon-experimental`).  Atoms are the nodes and links of a
metagraph; a :class:`LensSpace` stores and queries them.  The four core kinds
are:

* :class:`SymbolAtom` — an interned name (a node label).
* :class:`ExpressionAtom` — a recursive list of atoms (a hyperedge).
* :class:`VariableAtom` — a pattern variable used in queries.
* :class:`GroundedAtom` — an atom wrapped around a native Python value.

All atoms are immutable and hashable so they can live in sets and act as keys.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class Atom(ABC):
    """Base class for Lens atoms."""

    @abstractmethod
    def __str__(self) -> str:
        raise NotImplementedError

    @abstractmethod
    def _key(self) -> tuple:
        raise NotImplementedError

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Atom) and self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())


@dataclass(frozen=True)
class SymbolAtom(Atom):
    """A symbolic atom, e.g. ``Pulse`` or ``hasSource``."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("SymbolAtom name must be a str")

    def __str__(self) -> str:
        return self.name

    def _key(self) -> tuple:
        return ("SymbolAtom", self.name)


@dataclass(frozen=True)
class VariableAtom(Atom):
    """A pattern variable, e.g. ``$x``.  Variables only participate in matching."""

    name: str

    def __post_init__(self) -> None:
        if not isinstance(self.name, str):
            raise TypeError("VariableAtom name must be a str")

    def __str__(self) -> str:
        return f"${self.name}"

    def _key(self) -> tuple:
        return ("VariableAtom", self.name)


@dataclass(frozen=True)
class GroundedAtom(Atom):
    """An atom carrying a native Python value (a "grounded" value in MeTTa terms).

    The value is opaque to pattern matching: only the type name and a stable
    string representation are used for equality/hashing.  This keeps grounded
    atoms deterministic and hashable even when the wrapped object is mutable.
    """

    value: Any = field(compare=False, hash=False)
    typename: str = "Grounded"
    _repr: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_repr",
            self._repr or self._default_repr(),
        )

    def _default_repr(self) -> str:
        try:
            return str(self.value)
        except Exception:
            return object.__repr__(self.value)

    def __str__(self) -> str:
        return self.render

    @property
    def render(self) -> str:
        """Public, stable textual rendering used by interpretation and display."""
        return self._repr

    def _key(self) -> tuple:
        return ("GroundedAtom", self.typename, self._repr)


@dataclass(frozen=True)
class ExpressionAtom(Atom):
    """A recursive expression — a hyperedge linking other atoms.

    In MeTTa syntax this is ``(head arg1 arg2 ...)``.  Expressions are the
    primary way to represent relations, typed records, and nested structure.
    """

    children: tuple[Atom, ...]

    def __init__(self, *children: Atom) -> None:
        object.__setattr__(self, "children", tuple(children))

    def __post_init__(self) -> None:
        if not all(isinstance(c, Atom) for c in self.children):
            raise TypeError("ExpressionAtom children must be Atoms")

    def __str__(self) -> str:
        inner = " ".join(str(c) for c in self.children)
        return f"({inner})"

    def _key(self) -> tuple:
        return ("ExpressionAtom", self.children)

    def __iter__(self):
        return iter(self.children)
