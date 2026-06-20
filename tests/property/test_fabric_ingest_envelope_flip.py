"""#163 regression: relay/sync ingest must route node<->edge on SIGNATURE-COVERED
content, never the relayer-controlled UNSIGNED envelope ``kind``.

These tests drive the REAL no-``is_edge`` ingest seam — ``_ingest_signed(item)`` —
that ``sync_from`` / ``_pull_cids`` / ``_serve_inv_data`` all call without an
explicit ``is_edge``. A malicious relayer flips ``fabric-edge`` <-> ``fabric-record``
on a validly-signed body. With the fix, both an honest and a flipped envelope land
the same signed CID on the SAME side, so ``web_state_root`` does NOT diverge.

LOAD-BEARING: revert ``_ingest_signed`` to route on ``item['kind']`` and
``test_relay_kind_flip_does_not_diverge`` FAILS (the flip re-opens the partition):
the assertion ``web_state_root(honest.web) == web_state_root(flipped.web)`` flips
to unequal because the flipped receiver weaves the edge body as a node.
"""

from knitweb.fabric.items import web_state_root
from knitweb.fabric.node import FabricNode


def _seed(node, author):
    a = node.web.weave({"kind": "knowledge", "title": "X", "author": author.pub})
    b = node.web.weave({"kind": "knowledge", "title": "Y", "author": author.pub})
    return a, b


def test_relay_kind_flip_does_not_diverge() -> None:
    """A validly-signed EDGE relayed with the envelope kind FLIPPED to
    'fabric-record' still ingests as an EDGE (signed shape), so the flipped
    receiver's web_state_root matches the honest receiver's — no partition."""
    author = FabricNode()
    a = author.web.weave({"kind": "knowledge", "title": "X", "author": author.pub})
    b = author.web.weave({"kind": "knowledge", "title": "Y", "author": author.pub})
    edge = author.web.link(a, b, "supports", weight=2)
    honest_env = author._signed_edge_msg(edge.to_record())  # kind == 'fabric-edge'

    # Relayer flips ONLY the unsigned envelope kind; body + sig are untouched.
    flipped_env = dict(honest_env)
    flipped_env["kind"] = "fabric-record"
    assert flipped_env["record"] == honest_env["record"]
    assert flipped_env["sig"] == honest_env["sig"]

    honest = FabricNode()
    _seed(honest, author)
    honest._ingest_signed(honest_env)        # real relay/sync seam: no is_edge

    flipped = FabricNode()
    _seed(flipped, author)
    flipped._ingest_signed(flipped_env)      # same seam, hostile envelope

    # THE LOAD-BEARING ASSERTION: routing on signed content keeps the same signed
    # CID on the same (edge) side -> identical roots. Routing on item['kind']
    # (the reverted bug) weaves the body as a node on `flipped`, diverging here.
    assert web_state_root(honest.web) == web_state_root(flipped.web)
    # And it is genuinely an edge on BOTH, not a node masquerade.
    assert sum(len(v) for v in honest.web._out.values()) == 1
    assert sum(len(v) for v in flipped.web._out.values()) == 1
    assert flipped.web._out[a][0].cid == edge.cid


def test_legit_relayed_edge_and_node_converge() -> None:
    """LEGIT PRESERVED: an honestly-relayed edge and an honestly-relayed node
    (envelope kind matching signed content) both route to the correct side and
    converge with the author on the same no-is_edge seam."""
    author = FabricNode()
    a = author.web.weave({"kind": "knowledge", "title": "X", "author": author.pub})
    b = author.web.weave({"kind": "knowledge", "title": "Y", "author": author.pub})
    edge = author.web.link(a, b, "supports", weight=2)
    edge_env = author._signed_edge_msg(edge.to_record())
    node_rec = {"kind": "knowledge", "title": "Z", "author": author.pub}
    author.web.weave(node_rec)
    node_env = author._signed_record_msg(node_rec)

    rx = FabricNode()
    _seed(rx, author)
    rx._ingest_signed(node_env)   # honest node -> web.nodes
    rx._ingest_signed(edge_env)   # honest edge -> _out

    assert web_state_root(rx.web) == web_state_root(author.web)
    assert sum(len(v) for v in rx.web._out.values()) == 1
    assert rx.web._out[a][0].cid == edge.cid
    assert len(rx.web.nodes) == 3


def test_node_flip_to_edge_does_not_diverge() -> None:
    """Symmetric: a signed NODE whose envelope is flipped to 'fabric-edge' still
    routes by its signed content, so honest and flipped receivers converge."""
    author = FabricNode()
    _seed(author, author)
    node_rec = {"kind": "knowledge", "title": "Z", "author": author.pub}
    author.web.weave(node_rec)
    honest_env = author._signed_record_msg(node_rec)   # kind == 'fabric-record'
    flipped_env = dict(honest_env)
    flipped_env["kind"] = "fabric-edge"

    honest = FabricNode(); _seed(honest, author); honest._ingest_signed(honest_env)
    flipped = FabricNode(); _seed(flipped, author); flipped._ingest_signed(flipped_env)

    assert web_state_root(honest.web) == web_state_root(flipped.web)
    # A node body has no src/dst/rel edge shape, so it is a node on both, never an
    # edge masquerade even with the hostile 'fabric-edge' envelope.
    assert sum(len(v) for v in flipped.web._out.values()) == 0
    assert len(flipped.web.nodes) == 3
