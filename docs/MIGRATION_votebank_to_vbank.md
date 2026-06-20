# Migration: Votebank → vBank

The voting/crowdfunding product built on the personhood foundation is rebranded from
**Votebank** to **vBank**. This was done **before any data exists** on purpose: the record
`kind` strings are part of content identity (they are hashed into every record's CID), so a
rename is only free pre-launch. Doing it now is the same "privacy/identity decisions are
irreversible once the fabric carries data" discipline the foundation itself follows.

## Rename map

| Kind | Old | New |
|---|---|---|
| Module / package | `knitweb.knitwebs.votebank` | `knitweb.knitwebs.vbank` |
| Directory | `src/knitweb/knitwebs/votebank/` | `src/knitweb/knitwebs/vbank/` |
| Class | `VotebankKnitweb` | `VbankKnitweb` |
| Record `kind` (hash-critical) | `votebank-ballot` | `vbank-ballot` |
| Test | `tests/property/test_votebank_gate_stub.py` | `tests/property/test_vbank_gate_stub.py` |
| Brand in prose / docstrings | `Votebank` | `vBank` |
| Example scope id in tests | `"votebank"` | `"vbank"` |

Identifier convention: **`vbank`** for code (module, `kind`, scope), **`Vbank`** in PascalCase
class names, and **`vBank`** as the display brand in prose.

## What did NOT change

- The crowdfunding consumer keeps its generic name `knitweb.knitwebs.crowdfunding` (a function,
  not the brand); only its prose reference to the sibling product changed to vBank.
- The `personhood` foundation's public API, record schema, and the `crowdfunding-pledge` /
  `personhood-anchor` / `personhood-revoke` kinds are unchanged — only the vBank ballot kind
  and prose were touched.

## Out of scope (separate lane)

`docs/DOMAIN_KNITWEB_INTERFACE.md` and `docs/ROADMAP.md` reference "Votebank" as the Step-5
application; those files are owned by the domain-knitweb-interface work (currently uncommitted
in the shared tree) and should adopt the vBank name in that lane to avoid a cross-agent
conflict.

## Safety

No data migration is required (greenfield, nothing emitted to a live fabric). The full
property suite passes after the rename; verified against a fresh checkout (committed files
only) so CI sees the same green.
