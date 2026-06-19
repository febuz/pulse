"""Chemistry knitweb — emit signed, mass-and-charge-balanced reactions into the Web.

A chemistry knitweb takes a chemical reaction expressed as integer stoichiometry over
species (each with an element composition and a charge) and:

  1. **Gates on conservation** — refuses to sign a reaction unless every element is
     balanced and total charge is conserved across the arrow. This is the domain
     invariant a peer can re-check deterministically; an unbalanced "reaction" is
     physically impossible and never enters the fabric.
  2. **Emits a signed, content-addressed record** — the balanced reaction becomes a
     ``reaction-knowledge`` record, ECDSA-signed by its author (``fabric.attest``)
     and woven into the Web.

Everything on the signed path is integer-only (counts, coefficients, charge), so it
round-trips through canonical CBOR. Rate kinetics (Arrhenius), which are inherently
real-valued, are carried only as optional integer-scaled *milli-units* for the
record — they are metadata, never the soundness gate. (The kinetic model is adapted
from molgang's reaction_kinetics; here only its integer-safe parameters survive.)
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...core import canonical, crypto
from ...fabric.attest import Attestation, attest
from ...fabric.web import Web

__all__ = [
    "Species",
    "Term",
    "Reaction",
    "ChemistryKnitweb",
    "element_balance",
    "charge_balance",
    "is_balanced",
]


@dataclass(frozen=True)
class Species:
    """A chemical species: a formula, its element composition, and net charge."""

    formula: str
    composition: tuple[tuple[str, int], ...]  # sorted (element, count) pairs
    charge: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.charge, int) or isinstance(self.charge, bool):
            raise TypeError("species charge must be int")
        seen: set[str] = set()
        for element, count in self.composition:
            if element in seen:
                raise ValueError(f"{self.formula}: duplicate element {element}")
            seen.add(element)
            if not isinstance(count, int) or isinstance(count, bool):
                raise TypeError(f"{self.formula}: element {element} count must be int")
            if count <= 0:
                raise ValueError(f"{self.formula}: element {element} count must be > 0")

    @classmethod
    def make(cls, formula: str, composition: dict[str, int], charge: int = 0) -> "Species":
        """Build a Species from a {element: count} dict (stored canonically sorted)."""
        comp = tuple(sorted(composition.items()))
        return cls(formula=formula, composition=comp, charge=charge)

    def counts(self) -> dict[str, int]:
        return {e: c for e, c in self.composition}


@dataclass(frozen=True)
class Term:
    """A stoichiometric term: ``coeff`` units of ``species`` on one side."""

    species: Species
    coeff: int

    def __post_init__(self) -> None:
        if not isinstance(self.coeff, int) or isinstance(self.coeff, bool):
            raise TypeError("stoichiometric coefficient must be int")
        if self.coeff <= 0:
            raise ValueError("stoichiometric coefficient must be a positive integer")


@dataclass(frozen=True)
class Reaction:
    """A reaction: reactant terms → product terms, with optional integer kinetics."""

    reactants: tuple[Term, ...]
    products: tuple[Term, ...]
    # Optional integer-scaled Arrhenius metadata (never part of the soundness gate):
    #   pre_exponential_milli  — A × 1000 (1/s)
    #   activation_energy_j_per_mol — Ea (J/mol, already integer)
    kinetics: tuple[tuple[str, int], ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if not self.reactants or not self.products:
            raise ValueError("a reaction needs at least one reactant and one product")
        for key, value in self.kinetics:
            if not isinstance(value, int) or isinstance(value, bool):
                raise TypeError(f"kinetic value {key!r} must be int")


# ---------------------------------------------------------------------------
# Conservation checks (the deterministic, integer soundness gate)
# ---------------------------------------------------------------------------

def element_balance(reaction: Reaction) -> dict[str, int]:
    """Net element counts (products − reactants). Balanced ⇔ all values are 0."""
    net: dict[str, int] = {}
    for term in reaction.reactants:
        for element, count in term.species.counts().items():
            net[element] = net.get(element, 0) - term.coeff * count
    for term in reaction.products:
        for element, count in term.species.counts().items():
            net[element] = net.get(element, 0) + term.coeff * count
    return {e: v for e, v in net.items() if v != 0}


def charge_balance(reaction: Reaction) -> int:
    """Net charge (products − reactants). Conserved ⇔ 0."""
    r = sum(t.coeff * t.species.charge for t in reaction.reactants)
    p = sum(t.coeff * t.species.charge for t in reaction.products)
    return p - r


def is_balanced(reaction: Reaction) -> bool:
    """True iff every element is balanced and total charge is conserved."""
    return not element_balance(reaction) and charge_balance(reaction) == 0


# ---------------------------------------------------------------------------
# The knitweb
# ---------------------------------------------------------------------------

def _sorted(terms: tuple[Term, ...]) -> list[Term]:
    """Canonical term order (by species formula) so a reaction written with its
    reactants/products in any order yields one content id — equivalent reactions
    dedupe in the content-addressed Web instead of forking into distinct CIDs."""
    return sorted(
        terms,
        key=lambda t: (
            t.species.formula,
            t.species.composition,
            t.species.charge,
            t.coeff,
        ),
    )


def _equation(reaction: Reaction) -> str:
    def side(terms: list[Term]) -> str:
        return " + ".join(
            (f"{t.coeff} {t.species.formula}" if t.coeff != 1 else t.species.formula)
            for t in terms
        )
    return f"{side(_sorted(reaction.reactants))} -> {side(_sorted(reaction.products))}"


class ChemistryKnitweb:
    """Emits signed, conservation-checked reaction knowledge for one author key."""

    KIND = "reaction-knowledge"

    def __init__(self, author_priv: str) -> None:
        self._priv = author_priv
        self.author_pub = crypto.public_from_private(author_priv)
        self.address = crypto.address(self.author_pub)

    def to_record(self, reaction: Reaction) -> dict:
        """Build the integer-only, canonical-encodable record for a reaction."""
        def term_rec(t: Term) -> dict:
            return {
                "species": t.species.formula,
                "coeff": t.coeff,
                "composition": [list(pair) for pair in t.species.composition],
                "charge": t.species.charge,
            }
        record = {
            "kind": self.KIND,
            "equation": _equation(reaction),
            # Terms are canonically sorted so equivalent reactions share one CID.
            "reactants": [term_rec(t) for t in _sorted(reaction.reactants)],
            "products": [term_rec(t) for t in _sorted(reaction.products)],
            "author": self.address,
            "balanced": True,
        }
        if reaction.kinetics:
            record["kinetics"] = [list(pair) for pair in sorted(reaction.kinetics)]
        # Fail fast if anything non-canonical slipped in (e.g. a stray float).
        canonical.encode(record)
        return record

    def emit(self, reaction: Reaction) -> Attestation:
        """Validate conservation, then sign the reaction record. Raises if unsound."""
        net = element_balance(reaction)
        if net:
            raise ValueError(f"element imbalance, cannot sign: {net}")
        dq = charge_balance(reaction)
        if dq != 0:
            raise ValueError(f"charge imbalance ({dq}), cannot sign")
        return attest(self.to_record(reaction), self._priv, author_field="author")

    def weave(self, reaction: Reaction, web: Web) -> tuple[str, Attestation]:
        """Emit a signed reaction and weave it into *web*; return (cid, attestation)."""
        att = self.emit(reaction)
        cid = web.weave(att.record)
        return cid, att
