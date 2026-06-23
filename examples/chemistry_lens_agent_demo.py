"""Chemistry Lens Agent — knitweb.art homepage live demo.

Demonstrates an LLM-style agent that:

  1. Seeds a chemistry knowledge graph with signed, mass-balanced reactions
     (vanadium redox, biostimulant synthesis, CO₂-to-acetic-acid).
  2. Retrieves relevant reactions from the P2P Web via **Lens** (interpret.retrieve).
  3. Distils the candidate set and counts how many relation tokens are eliminated
     versus naïve full-prompt concatenation (token savings).
  4. Synthesises a new Python module with the recovered reactions as typed code.
  5. Pushes the generated module to GitHub via the REST API.

Run (no external deps beyond `cryptography` and optionally `httpx` for the push):

    PYTHONPATH=src python examples/chemistry_lens_agent_demo.py

Set GITHUB_TOKEN + GITHUB_REPO (owner/repo) to enable the real push; otherwise
the push step prints the payload to stdout.
"""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from base64 import b64encode
from typing import Sequence

from knitweb.core import crypto
from knitweb.fabric.web import Web
from knitweb.interpret.retrieve import retrieve
from knitweb.knitwebs.chemistry import (
    ChemistryKnitweb,
    Reaction,
    Species,
    Term,
    is_balanced,
)

# ── ANSI colours (stripped when stdout is not a tty) ──────────────────────
_tty = sys.stdout.isatty()
def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _tty else text

CYAN   = lambda t: _c("96", t)
GREEN  = lambda t: _c("92", t)
YELLOW = lambda t: _c("93", t)
GREY   = lambda t: _c("90", t)
BOLD   = lambda t: _c("1",  t)
DIM    = lambda t: _c("2",  t)


def _step(n: int, label: str) -> None:
    print(f"\n{BOLD(CYAN(f'[{n}]'))} {BOLD(label)}")


def _log(msg: str) -> None:
    print(f"    {GREY('›')} {msg}")


# ═══════════════════════════════════════════════════════════════════════════
# 1.  Seeding — build the chemistry knowledge graph
# ═══════════════════════════════════════════════════════════════════════════

def _make_reactions() -> list[tuple[str, Reaction]]:
    """Return labelled, balanced reactions for the demo knowledge base.

    All reactions use neutral molecules only (no ionic e⁻ species) to keep
    the element-balance gate simple and portable.
    """
    h2  = Species.make("H2",      {"H": 2})
    h2o = Species.make("H2O",     {"H": 2, "O": 1})

    # — Haber-Bosch (NH₃ synthesis) — NH BLOOM biostimulant ——————————————
    # N₂ + 3H₂ → 2NH₃   bal: N:2=2 H:6=6 ✓
    n2  = Species.make("N2",  {"N": 2})
    nh3 = Species.make("NH3", {"N": 1, "H": 3})
    haber = Reaction(
        reactants=(Term(n2, 1), Term(h2, 3)),
        products=(Term(nh3, 2),),
    )

    # — CO₂ hydrogenation → acetic acid (EHMAC CO₂ utilisation) ————————
    # 2CO₂ + 4H₂ → CH₃COOH + 2H₂O   bal: C:2=2 O:4=2+2 H:8=4+4 ✓
    co2     = Species.make("CO2",     {"C": 1, "O": 2})
    acetic  = Species.make("CH3COOH", {"C": 2, "H": 4, "O": 2})
    co2_rxn = Reaction(
        reactants=(Term(co2, 2), Term(h2, 4)),
        products=(Term(acetic, 1), Term(h2o, 2)),
    )

    # — V₂O₅ reduction → V₂O₄ (VRFB electrolyte pre-reduction) —————————
    # V₂O₅ + H₂ → V₂O₄ + H₂O   bal: V:2=2 O:5+0=4+1=5 H:2=2 ✓
    v2o5 = Species.make("V2O5", {"V": 2, "O": 5})
    v2o4 = Species.make("V2O4", {"V": 2, "O": 4})
    v_red = Reaction(
        reactants=(Term(v2o5, 1), Term(h2, 1)),
        products=(Term(v2o4, 1), Term(h2o, 1)),
    )

    # — Vanadyl sulfate prep (V₂O₄ + H₂SO₄ → VOSO₄) ————————————————————
    # V₂O₄ + 2H₂SO₄ → 2VOSO₄ + 2H₂O
    # V:2=2 O:4+8=12; VOSO₄=V1O5S1 → 2×O5+2×O1=12 H:4=4 S:2=2 ✓
    h2so4 = Species.make("H2SO4", {"H": 2, "S": 1, "O": 4})
    voso4 = Species.make("VOSO4", {"V": 1, "O": 5, "S": 1})
    voso4_rxn = Reaction(
        reactants=(Term(v2o4, 1), Term(h2so4, 2)),
        products=(Term(voso4, 2), Term(h2o, 2)),
    )

    reactions = [
        ("Haber-Bosch NH3 synthesis",           haber),
        ("CO2 hydrogenation to acetic acid",    co2_rxn),
        ("V2O5 reduction to V2O4",              v_red),
        ("Vanadyl sulfate preparation",         voso4_rxn),
    ]

    for label, rxn in reactions:
        if not is_balanced(rxn):
            from knitweb.knitwebs.chemistry import element_balance, charge_balance
            raise RuntimeError(
                f"Unbalanced: {label} | el={element_balance(rxn)} q={charge_balance(rxn)}"
            )

    return reactions


def seed_graph(priv_key: str) -> tuple[Web, list[str]]:
    """Emit signed reactions and weave them into a local Web node.

    In production this step runs on spider nodes and propagates over 5mart.ml.
    """
    ck = ChemistryKnitweb(priv_key)
    web = Web()
    cids: list[str] = []

    for label, rxn in _make_reactions():
        cid, att = ck.weave(rxn, web)
        cids.append(cid)
        _log(f"{GREEN('✓')} {label:<42} cid={GREY(cid[:18]+'…')}")

    return web, cids


# ═══════════════════════════════════════════════════════════════════════════
# 2.  Lens retrieval — query the Web via interpret.retrieve
# ═══════════════════════════════════════════════════════════════════════════

def lens_query(
    web: Web,
    query: "str | dict",
    scopes: Sequence[str],
) -> dict[str, dict]:
    """Run a Lens subscription query and return the matching records."""
    candidate_set = retrieve(
        query=query,
        subscription=list(scopes),
        web=web,
        depth=2,
    )
    return candidate_set.records(web)


# ═══════════════════════════════════════════════════════════════════════════
# 3.  Token savings — measure naive vs. distilled context size
# ═══════════════════════════════════════════════════════════════════════════

def _rough_tokens(text: str) -> int:
    """Rough token count: ~4 chars per token (GPT-4 approximation)."""
    return max(1, len(text) // 4)


def measure_savings(records: dict[str, dict]) -> tuple[int, int, float]:
    """Return (naive_tokens, lens_tokens, pct_saved)."""
    full_json  = json.dumps(list(records.values()), indent=2)
    naive_toks = _rough_tokens(full_json)

    # Lens distilled view: only equation + kind per record (the relation skeleton)
    distilled = [
        {"equation": r.get("equation", ""), "kind": r.get("kind", "")}
        for r in records.values()
    ]
    lens_json  = json.dumps(distilled)
    lens_toks  = _rough_tokens(lens_json)

    pct = 100 * (1 - lens_toks / naive_toks) if naive_toks else 0.0
    return naive_toks, lens_toks, pct


# ═══════════════════════════════════════════════════════════════════════════
# 4.  Code synthesis — write a typed Python module with the reactions
# ═══════════════════════════════════════════════════════════════════════════

_MODULE_HEADER = '''\
"""Auto-generated by the Knitweb Chemistry Lens Agent.

Reactions were retrieved from the knitweb P2P fabric via Lens (interpret.retrieve)
and validated against the ChemistryKnitweb conservation gate before emission.
"""
from __future__ import annotations

from knitweb.knitwebs.chemistry import ChemistryKnitweb, Reaction, Species, Term, is_balanced

# Author key (demo — replace with real secp256k1 private key)
DEMO_PRIV = "0000000000000000000000000000000000000000000000000000000000000001"
_ck = ChemistryKnitweb(DEMO_PRIV)


def make_species(formula: str, comp: dict[str, int], charge: int = 0) -> Species:
    return Species.make(formula, comp, charge)

'''

_REACTION_TEMPLATE = '''\
def reaction_{slug}() -> Reaction:
    """{label}  [{equation}]"""
{reactant_lines}
{product_lines}
    rxn = Reaction(
        reactants=({reactant_terms}),
        products=({product_terms}),
    )
    assert is_balanced(rxn), "Conservation check failed — contact the spider"
    return rxn

'''


def _slug(label: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def synthesise_module(records: dict[str, dict]) -> str:
    lines = [_MODULE_HEADER]
    for record in records.values():
        if record.get("kind") != "reaction-knowledge":
            continue
        eq  = record.get("equation", "unknown")
        rts = record.get("reactants", [])
        pts = record.get("products", [])
        label = eq

        def var(t: dict) -> str:
            f = t["species"].replace("+", "plus").replace("-", "minus").replace(".", "_")
            import re
            return re.sub(r"[^a-zA-Z0-9_]", "_", f).lower()

        seen: dict[str, str] = {}
        r_defs: list[str] = []
        for t in rts + pts:
            v = var(t)
            if v not in seen:
                seen[v] = v
                comp = {e: c for e, c in t["composition"]}
                r_defs.append(
                    f"    {v} = make_species({t['species']!r}, {comp!r}, charge={t['charge']})"
                )

        r_terms = ", ".join(f"Term({var(t)}, {t['coeff']})" for t in rts)
        p_terms = ", ".join(f"Term({var(t)}, {t['coeff']})" for t in pts)

        block = _REACTION_TEMPLATE.format(
            slug=_slug(label)[:40],
            label=label,
            equation=eq,
            reactant_lines="\n".join(r_defs[:len(rts)+1]),
            product_lines="\n".join(r_defs[len(rts):]),
            reactant_terms=r_terms,
            product_terms=p_terms,
        )
        lines.append(block)

    lines.append(
        "\nif __name__ == '__main__':\n"
        "    from knitweb.fabric.web import Web\n"
        "    web = Web()\n"
        "    for fn in [v for k, v in list(globals().items()) if k.startswith('reaction_')]:\n"
        "        rxn = fn()\n"
        "        cid, _ = _ck.weave(rxn, web)\n"
        "        print(f'{fn.__name__}: {cid[:20]}…')\n"
    )
    return "".join(lines)


# ═══════════════════════════════════════════════════════════════════════════
# 5.  GitHub push — REST API (no git binary needed)
# ═══════════════════════════════════════════════════════════════════════════

def push_to_github(content: str, path: str = "generated/chemistry_reactions.py") -> str:
    """Push *content* to GITHUB_REPO via the Contents API.

    Returns the URL of the created/updated blob, or a dry-run notice.
    """
    token = os.environ.get("GITHUB_TOKEN", "")
    repo  = os.environ.get("GITHUB_REPO", "")

    if not token or not repo:
        return (
            "DRY RUN — set GITHUB_TOKEN + GITHUB_REPO env vars to push for real.\n"
            f"Would push {len(content.encode())} bytes to {path}"
        )

    try:
        import urllib.request

        encoded = b64encode(content.encode()).decode()
        api_url = f"https://api.github.com/repos/{repo}/contents/{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        }

        # Check if file already exists (to get the sha for update)
        sha: str | None = None
        try:
            req = urllib.request.Request(api_url, headers=headers)
            with urllib.request.urlopen(req, timeout=10) as resp:
                existing = json.loads(resp.read())
                sha = existing.get("sha")
        except Exception:
            pass

        payload: dict = {
            "message": "chore: auto-generated chemistry reactions from Lens agent",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha

        data = json.dumps(payload).encode()
        req = urllib.request.Request(api_url, data=data, headers=headers, method="PUT")
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
            html_url: str = result.get("content", {}).get("html_url", api_url)
            return f"Pushed → {html_url}"
    except Exception as exc:
        return f"Push failed: {exc}"


# ═══════════════════════════════════════════════════════════════════════════
# main
# ═══════════════════════════════════════════════════════════════════════════

def main() -> None:
    print(BOLD(CYAN("\n  ╔══════════════════════════════════════════════╗")))
    print(BOLD(CYAN("  ║  Knitweb · Chemistry Lens Agent Demo         ║")))
    print(BOLD(CYAN("  ╚══════════════════════════════════════════════╝\n")))

    t0 = time.monotonic()

    # ── Step 1: seed ──────────────────────────────────────────────────────
    _step(1, "Seeding chemistry knowledge graph (P2P spider role)")
    priv, _ = crypto.generate_keypair()
    web, seed_cids = seed_graph(priv)
    _log(f"{len(seed_cids)} reactions woven into the local Web node")
    _log(f"In production: propagated over 5mart.ml relay → peer nodes pick up CIDs")

    # ── Step 2: Lens retrieval ─────────────────────────────────────────────
    _step(2, "Lens retrieval (interpret.retrieve) — querying the P2P fabric")

    queries = [
        ({"kind": "reaction-knowledge", "text": "VOSO4"},    ["reaction-knowledge"],  "VRFB electrolyte — vanadyl sulfate"),
        ({"kind": "reaction-knowledge", "text": "V2O5"},     ["reaction-knowledge"],  "VRFB — V₂O₅ reduction"),
        ({"kind": "reaction-knowledge", "text": "NH3"},      ["reaction-knowledge"],  "Biostimulant NH BLOOM synthesis"),
        ({"kind": "reaction-knowledge", "text": "CH3COOH"},  ["reaction-knowledge"],  "CO₂ utilisation — acetic acid"),
    ]

    all_records: dict[str, dict] = {}
    for q, scopes, desc in queries:
        hits = lens_query(web, q, scopes)
        all_records.update(hits)
        _log(f"{GREEN(str(len(hits)))} records ← {YELLOW(repr(q['text']))} [{desc}]")

    _log(f"Total unique records retrieved: {BOLD(str(len(all_records)))}")

    # ── Step 3: token savings ─────────────────────────────────────────────
    _step(3, "Token savings — naive prompt vs. Lens distilled context")
    naive, lens, saved = measure_savings(all_records)
    bar_full  = "█" * int(naive / 50)
    bar_lens  = "█" * int(lens  / 50)
    _log(f"Naive (full JSON concat) : {YELLOW(str(naive))} tokens  {GREY(bar_full)}")
    _log(f"Lens distilled skeleton  : {GREEN(str(lens))} tokens  {GREEN(bar_lens)}")
    _log(f"Tokens saved             : {BOLD(GREEN(f'{saved:.0f}%'))} "
         f"({naive - lens} tokens eliminated before LLM call)")
    _log(f"Per query at $0.003/1k tok (GPT-4): saved "
         f"{BOLD(GREEN(f'${(naive - lens) * 0.003 / 1000:.4f}'))}/request")

    # ── Step 4: code synthesis ────────────────────────────────────────────
    _step(4, "Synthesising typed Python module from retrieved reactions")
    module_src = synthesise_module(all_records)
    lines = module_src.count("\n")
    chars = len(module_src)
    _log(f"Generated {lines} lines / {chars} chars")
    _log(f"Module: {GREY('generated/chemistry_reactions.py')}")
    _log("")
    # Print first 8 lines of generated code
    for line in module_src.splitlines()[:8]:
        _log(GREY("  " + line))
    _log(GREY("  …"))

    # ── Step 5: GitHub push ───────────────────────────────────────────────
    _step(5, "Pushing generated code to GitHub")
    result = push_to_github(module_src)
    _log(result)

    # ── Summary ───────────────────────────────────────────────────────────
    elapsed = time.monotonic() - t0
    print(f"\n  {BOLD(GREEN('✓ Done'))}  in {elapsed:.2f}s\n")
    print("  " + GREY("─" * 54))
    print(f"  Reactions in graph : {BOLD(str(len(seed_cids)))}")
    print(f"  Records retrieved  : {BOLD(str(len(all_records)))}")
    print(f"  Token savings      : {BOLD(GREEN(f'{saved:.0f}%'))}")
    print(f"  Code generated     : {BOLD(str(lines))} lines")
    print("  " + GREY("─" * 54))
    print(f"\n  Run with:  {YELLOW('PYTHONPATH=src python examples/chemistry_lens_agent_demo.py')}")
    print(f"  Push real: {YELLOW('GITHUB_TOKEN=… GITHUB_REPO=owner/repo …')}\n")


if __name__ == "__main__":
    main()
