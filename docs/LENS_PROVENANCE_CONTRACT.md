# Lens provenance query contract

A *Lens* reads provenance out of the woven Web from outside Pulse. This document
defines the stable query boundary it depends on: fixed inputs, a fixed output shape,
deterministic ordering, relation-filter semantics, and explicit dangling-reference
(missing-node) visibility. The contract lives in
`knitweb.fabric.provenance_contract` and composes the existing full-depth,
relation-filtered ancestry walk (`knitweb.fabric.provenance`) — a Lens never
re-implements the graph logic or depends on incidental dict/iteration order.

## Entry point

```python
provenance_query(web: Web, start: str, rels: set[str] | None = None)
    -> ProvenanceQueryResult
```

### Inputs

| Input   | Meaning                                                                       |
| ------- | ----------------------------------------------------------------------------- |
| `web`   | the woven `Web` to read provenance from (read only — never mutated).          |
| `start` | the CID whose provenance is queried; it is the `root` and is never an ancestor of itself. |
| `rels`  | a set of relation names to follow (e.g. `{"derived-from"}`); `None` follows every edge type. |

### Output: `ProvenanceQueryResult` (frozen dataclass)

| Field            | Meaning                                                                              |
| ---------------- | ------------------------------------------------------------------------------------ |
| `root`           | the CID the query started from (excluded from the ancestry).                         |
| `rels`           | the sorted relation-filter names applied as a tuple, or `None` for "all edges".      |
| `present`        | ancestor CIDs whose node record **is** present in the Web.                           |
| `missing`        | ancestor CIDs reachable via an edge but **not** present in the Web — dangling references. |
| `origin_present` | raw-material leaf ancestors (no further antecedents under `rels`) that are present.  |
| `origin_missing` | leaf ancestors that are dangling references.                                         |

`ProvenanceQueryResult.has_dangling` is a convenience: `True` iff `missing` is non-empty.

## Ordering guarantee

Every CID list in the result is **sorted by CID**. Consequently:

* repeated calls over identical Web content return equal results, and
* the result is **identical across different node/edge insertion orders** — two Webs
  built with the same records and edges in a different order produce the same result.

No wall-clock, randomness, or iteration-order leaks into the output.

## Relation-filter semantics

`rels` scopes which edges count as provenance, applied at every hop of the full-depth
walk. With `rels={"derived-from"}` only `derived-from` edges are followed, so an
unrelated `mentions` or `cites` edge from a record is ignored — both for ancestry and
for which leaves count as origins. `rels=None` follows every edge type. `rels` is
reported back (sorted) in the result so a Lens can confirm the filter it asked for.

## Missing-node (dangling-reference) visibility

The Web links edges between content-addressed CIDs, but an antecedent CID a record
derives from may not have its node record present: a peer-fed edge whose target node
has not synced yet, or a record dropped after its edge was woven. The raw ancestry walk
follows edges and so still reaches such a CID; this contract resolves every reachable
ancestor against the Web (`web.get`) and **partitions** them:

* an ancestor whose record resolves goes in `present` (and `origin_present` if it is a leaf);
* an ancestor whose `web.get` returns `None` goes in `missing` (and `origin_missing` if a leaf).

A dangling reference is therefore always **visible** in `missing` — never silently
dropped — so a Lens can flag incomplete provenance instead of mistaking it for a clean
chain.

## Read-only

Building a result only reads the Web (the underlying ancestry walk and `web.get`). It
never weaves, links, or rewrites any record or edge, so a Lens can query freely without
risk of corrupting live fabric state.

## Usage example

```python
from knitweb.fabric.web import Web
from knitweb.fabric.provenance_contract import provenance_query

web = Web()
ore = web.weave({"kind": "material", "sku": "IRON-ORE"})
smelt = web.weave({"kind": "process", "op": "smelting"})
part = web.weave({"kind": "material", "sku": "GEAR"})
web.link(smelt, ore, "derived-from")
web.link(part, smelt, "derived-from")

result = provenance_query(web, part, rels={"derived-from"})
# result.present        -> (ore, smelt) sorted by CID
# result.origin_present -> (ore,)        the raw-material leaf
# result.missing        -> ()            no dangling references
# result.has_dangling   -> False
```
