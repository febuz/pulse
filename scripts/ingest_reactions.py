#!/usr/bin/env python3
"""Ingest molgang chemistry recipes into a pulse Web and print the resulting CIDs.

Usage:
    PYTHONPATH=src python scripts/ingest_reactions.py [path/to/reactions.json]

Default reactions source: ../molgang-web/shared/reactions.json (sibling repo).
Each balanced recipe is signed by an ephemeral author key, woven into an
in-memory Web, and its CID printed to stdout. Unbalanced or malformed entries
are skipped with a warning (non-zero exit only on file/import errors).

This is the MVP bridge between molgang game recipes and the pulse P2P fabric.
For production, replace the ephemeral key with a stable author identity and
weave into a persisted Web node rather than an in-memory instance.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# ── PYTHONPATH is expected to include src/; guide the user if it doesn't. ─────
try:
    from knitweb.core import crypto
    from knitweb.fabric.web import Web
    from knitweb.knitwebs.chemistry import (
        ChemistryKnitweb,
        Reaction,
        Species,
        Term,
        is_balanced,
    )
except ImportError as exc:
    sys.exit(
        f"ImportError: {exc}\n"
        "Run as: PYTHONPATH=src python scripts/ingest_reactions.py"
    )

# ── locate reactions.json ─────────────────────────────────────────────────────

_SIBLING_DEFAULT = (
    Path(__file__).resolve().parent.parent.parent  # ../ above pulse-work
    / "molgang-web"
    / "shared"
    / "reactions.json"
)

def _reactions_path() -> Path:
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    if _SIBLING_DEFAULT.exists():
        return _SIBLING_DEFAULT
    sys.exit(
        "reactions.json not found.\n"
        f"Expected: {_SIBLING_DEFAULT}\n"
        "Pass the path explicitly: python scripts/ingest_reactions.py <path>"
    )


# ── recipe → Reaction conversion ─────────────────────────────────────────────

def _recipe_to_reaction(recipe: dict) -> Reaction | None:
    """Convert a molgang recipe dict to a ChemistryKnitweb Reaction.

    Reaction model:
      Reactants — each element as a mono-atomic species (H, O, Fe …)
      Product   — the molecule with its full atomic composition

    The element counts in `consumes` determine both sides, so mass balance
    is guaranteed: the product species composition equals the total reactant
    element count, making element_balance(rxn) == {} by construction.
    """
    name = recipe.get("name", "")
    consumes: dict = recipe.get("consumes") or {}
    if not name or not consumes:
        return None

    try:
        reactants = tuple(
            Term(Species.make(sym, {sym: count}), 1)
            for sym, count in consumes.items()
        )
        # product composition = sum of reactant element counts
        product_composition = dict(consumes)
        product = Species.make(name, product_composition)
        products = (Term(product, 1),)
        return Reaction(reactants=reactants, products=products)
    except (TypeError, ValueError) as exc:
        print(f"  [skip] {name}: could not build reaction — {exc}", file=sys.stderr)
        return None


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    path = _reactions_path()
    print(f"Loading reactions from: {path}")

    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)

    recipes: list[dict] = data.get("recipes", [])
    print(f"Found {len(recipes)} recipes\n")

    # Ephemeral author key — replace with stable identity in production.
    priv, pub = crypto.generate_keypair()
    author_addr = crypto.address(pub)
    kw = ChemistryKnitweb(priv)
    web = Web()

    ingested = 0
    skipped = 0

    for recipe in recipes:
        name = recipe.get("name", "<unnamed>")
        rxn = _recipe_to_reaction(recipe)
        if rxn is None:
            skipped += 1
            continue

        if not is_balanced(rxn):
            print(f"  [skip] {name}: unbalanced (element conservation violated)", file=sys.stderr)
            skipped += 1
            continue

        try:
            cid, att = kw.weave(rxn, web)
            display = recipe.get("displayName", name)
            formula = recipe.get("formula", name)
            category = recipe.get("category", "?")
            print(f"  ✓ {display:20s} ({formula:8s}) [{category:10s}]  CID: {cid}")
            ingested += 1
        except Exception as exc:
            print(f"  [skip] {name}: weave failed — {exc}", file=sys.stderr)
            skipped += 1

    print(f"\nIngested: {ingested}  Skipped: {skipped}")
    print(f"Author:   {author_addr}")
    print(f"Nodes in web: {len(web.nodes)}")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
