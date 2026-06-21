# The Lens / RLM Contract

**Brand vocabulary:** Web · Knit · Pulse · Fiber · knitweb. A *Lens* (the
interpret/RLM — *relational language model* — reasoning lobe) reads the woven Web
from outside Pulse. This doc states the contract that surface must hold to: it is
ephemeral, read-only, and provenance-preserving. It pins the contract to the real
export surface — `fabric.jsonld.export_web`, `fabric.snapshot.web_snapshot`,
`fabric.items.web_state_root`, and `fabric.provenance.*` — and does not contradict
the snapshot boundary (`snapshot.web_snapshot`, the deterministic read-only view).

## The four invariants

1. **Lens is an ephemeral interpret layer over content-addressed fabric chunks.**
   A Lens answers questions *about* the converged Web — a graph of canonical-CBOR
   nodes keyed by their CID and first-class typed edges between CIDs. It holds no
   durable state of its own: it is handed a snapshot, interprets it, and is done.
   The CIDs in that snapshot are the only identity it speaks; nothing the Lens
   produces re-enters fabric except through the existing PoUW distill/job flow,
   which re-signs and re-weaves on its own terms.

2. **Adapters are read models — there is no write path.** Every Lens-facing
   primitive is a *projection* of the Web, never a mutator. `export_web` is a
   pure read over the graph (it "never re-hashes or rewrites a node record"),
   `web_snapshot` "never weaves, links, or rewrites any record, signature, or
   feed," and `provenance.ancestry` / `provenance.provenance` / `provenance.origins`
   are pure graph walks. None of these surfaces expose `Web.weave`, `Web.link`, or
   any feed/signature write. An adapter that needs to *act* on a conclusion does so
   outside the read surface, through the normal fabric write APIs — never as a side
   effect of interpreting.

3. **No mutation occurs during interpretation.** `web_snapshot` returns a
   `copy.deepcopy` of its projection, so a Lens mutating the snapshot it receives
   can never reach back into the live Web's records or edge objects — the boundary
   is read-only by construction, not by convention. Interpreting the same Web
   content twice returns equal, byte-stable results (nodes sorted by CID, edges in
   `(rel, dst, weight)` order), with no wall-clock, randomness, or insertion-order
   leak. The Web a Lens read is byte-identical to the Web after it read.

4. **Every answer preserves provenance.** An answer is not free-floating text; it
   carries the chain back to its sources. A Lens cites:
   * `state_root` — the snapshot's `web_state_root`, an
     `sha256(node_root || edge_root)` commitment over the FULL Web (nodes **and**
     edges). Two Webs with different relations, weights, or links produce different
     roots, so the answer is bound to the exact graph it was computed over and an
     external party can recompute it from the snapshot's own bytes.
   * `provenance.ancestry(web, cid)` / `provenance.origins(web, cid)` — the
     content-addressed antecedents the cited record derives from, to full depth,
     down to the raw-material origin leaves. The answer names CIDs, and each CID is
     a content-derived id (`export_web` emits each node under its CID as `@id`), so
     a verifier resolves and re-hashes it offline with no trust in the Lens.

An answer that cannot cite a `state_root` and the ancestry/origins of the records
it leans on is not a conforming Lens answer.

## Export-to-Lens: the JSON-LD `@graph`

A Lens is never handed the live `Web`. It is handed a snapshot, whose `jsonld` key
is the deterministic `export_web` document — a stable `@context` plus a `@graph` of
node objects, each keyed by its CID (`@id`) with its raw `record` and its outgoing
typed `edges`. This is the export-to-Lens query surface: a Lens queries the
`@graph` and answers by citing the `state_root` alongside it.

```python
from knitweb.fabric.jsonld import import_web
from knitweb.fabric import provenance

# `snap` is the web_snapshot dict the host handed the Lens. Everything below is
# derived from `snap` alone — the Lens never touches the live Web.
commitment = snap["state_root"]          # 64 hex chars: sha256(node_root || edge_root)
graph = snap["jsonld"]["@graph"]         # the deterministic JSON-LD export-to-Lens view

# 1. Reconstruct a read-only Web from the snapshot's JSON-LD read model. import_web
#    re-weaves the records and re-links the edges, and is self-checking: it raises if
#    any node's @id does not match the CID derived from its own record.
web = import_web(snap["jsonld"])

# 2. The Lens query is a pure read over the @graph — a content-addressed lookup,
#    no mutation. Here: "what does this product node derive from?"
product_cid = "bafy...product"
node = next(obj for obj in graph if obj["id"] == product_cid)
record = node["record"]                  # the canonical record under its content-derived @id
derived_from = [e["dst"] for e in node["edges"] if e["rel"] == "derived-from"]
```

The JSON-LD a Lens reads (an abridged export document — `@context` plus a one-node
`@graph`). It is trimmed for the doc: the real `export_web` also emits a top-level
`rdfs:label` array and additional `@context` keys.

```json
{
  "@context": {
    "@vocab": "https://schema.org/",
    "knit": "did:dkg:knitweb#",
    "id": "@id",
    "type": "@type",
    "record": "knit:record",
    "edges": "knit:edges",
    "rel": "knit:rel",
    "weight": "knit:weight",
    "src": { "@id": "knit:src", "@type": "@id" },
    "dst": { "@id": "knit:dst", "@type": "@id" }
  },
  "@graph": [
    {
      "id": "bafy...product",
      "type": "KnitwebWebNode",
      "ual": "did:dkg:knitweb/bafy...product",
      "record": { "kind": "product", "labels": { "en": "alloy-x" } },
      "edges": [
        { "type": "KnitwebWebEdge", "rel": "derived-from", "dst": "bafy...slag", "weight": 1 }
      ]
    }
  ]
}
```

The answer the Lens returns preserves provenance by pairing its conclusion with the
commitment and the citation chain — every value below is an integer or a CID
string, float-free, so the answer itself stays canonical-CBOR-encodable:

```python
answer = {
    "claim": "alloy-x derives from steel slag",
    "subject": product_cid,
    "state_root": commitment,                                  # the Web it was computed over
    "ancestry": provenance.ancestry(web, product_cid),         # full-depth antecedent CIDs
    "origins": provenance.origins(web, product_cid),           # raw-material origin leaves
}
```

A verifier checks the answer with no trust in the Lens: recompute `web_state_root`
over the snapshot and confirm it equals `state_root`; resolve each cited CID and
re-derive it from its own canonical bytes; reconstruct the read-only Web from the
snapshot with `import_web(snap["jsonld"])` and re-walk `provenance.ancestry` /
`provenance.origins` over that Web. The Lens read nothing it cannot prove and wrote
nothing at all.
