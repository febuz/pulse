# `loom → knitweb` rename runbook (PR #2)

A ready-to-apply, **verified-safe** runbook for the owner-decided literal `loom → knitweb`
rename (incl. the core `Loom` validation primitive). This is **PR #2** in the migration plan —
it lands on `knitweb/knitweb` *after* the mirror push and the consistency pass (PR #1). This
doc is the precise mechanical recipe + the safety gate so the rename is a fast apply, not a
research task.

> Owner decision (2026-06-17): rename `loom` everywhere, literally, accepting the
> `knitweb.ledger.knitweb.Knitweb` overload. `LoomToken` is dropped, not renamed
> ("Maak geen loomtoken").

## 0. Why this is safe — the signed-record gate (re-proven on `main`)

The one hard invariant: **never change a field name/key/value inside a canonical/signed
record** (it changes every CID and signature). The rename is identifier + filename + prose
only. Evidence gathered on `main` (the migration source):

- **Inventory:** 316 `loom` hits across **25 files** (src + tests + docs + `pyproject.toml`).
- **No record `kind` contains "loom".** The record kinds are `reaction-knowledge`,
  `supplychain-process`, `capacity-allocation`, `journal-entry`, `invoice`.
- **Decisive test — quoted string literals containing "loom" in `src/`:** the *only* hits are
  `__all__` export entries (`"LoomError"`, `"ChemistryLoom"`, `"FinanceLoom"`,
  `"OperationalLoom"`, `"SupplyChainLoom"` — class-name strings that rename *with* their
  classes) and one docstring. **None is a record key or value.**
- The `network` id field in `Knit.to_record` (the one allowed technical "network") contains no
  "loom" and is untouched.

Re-run the gate before starting (must reproduce the same conclusion):

```bash
git grep -nE "[\"'][^\"']*[Ll]oom[^\"']*[\"']" -- 'src/*'   # only __all__ entries + 1 docstring
git grep -in -E 'kind\s*[:=]' -- 'src/*' | grep -i loom     # must be EMPTY
```

## 1. File / directory renames (`git mv` — preserves history)

```bash
git mv src/knitweb/ledger/loom.py  src/knitweb/ledger/knitweb.py
git mv src/knitweb/looms           src/knitweb/knitwebs      # parent pkg only; sub-looms keep their names
git mv tests/looms                 tests/knitwebs
```

(The domain subpackages `chemistry/ finance/ operational/ supplychain/` keep their directory
names — only the umbrella `looms/` package becomes `knitwebs/`.)

## 2. Symbol & path rewrites (ordered — longest/most-specific first)

Apply across `src/ tests/ docs/ pyproject.toml`. **Order matters**: rename the `*Loom`
compounds and module paths before the bare `Loom`/`loom` tokens so substrings don't collide.
`knitweb` contains no "loom", so no rewrite can corrupt an already-renamed token.

```bash
FILES=$(git ls-files 'src/*.py' 'tests/*.py' 'docs/*.md' 'pyproject.toml')

# 2a. domain validator classes (compounds first)
sed -i 's/SupplyChainLoom/SupplyChainKnitweb/g' $FILES
sed -i 's/ChemistryLoom/ChemistryKnitweb/g'     $FILES
sed -i 's/OperationalLoom/OperationalKnitweb/g' $FILES
sed -i 's/FinanceLoom/FinanceKnitweb/g'         $FILES

# 2b. core validator class + its error (LoomError before bare Loom)
sed -i 's/\bLoomError\b/KnitwebError/g'         $FILES
sed -i 's/\bLoom\b/Knitweb/g'                    $FILES        # the core ledger.loom.Loom validator

# 2c. module / package paths (before bare lowercase 'loom')
sed -i 's/ledger\.loom\b/ledger.knitweb/g'      $FILES        # import knitweb.ledger.loom -> .knitweb
sed -i 's/\blooms\b/knitwebs/g'                  $FILES        # knitweb.looms.* , tests/looms , "domain looms"

# 2d. pytest marker + local identifiers + remaining prose
sed -i 's/@pytest\.mark\.loom\b/@pytest.mark.knitweb/g' $FILES
sed -i 's/\bloom\b/knitweb/g'                    $FILES        # local var `loom`, marker name, prose "loom"
```

Then fix the pytest marker **registration** in `pyproject.toml` by hand (the `\bloom\b`
sweep renames the marker key; confirm the description reads sensibly):

```toml
# was:  "loom: domain-loom tests (Phase 5+)"
"knitweb: domain-knitweb tests (Phase 5+)",
```

### Manual-review carve-outs (do NOT blind-rename)
- **"Loom Network" brand-collision note** in `README.md` / `docs/research/08-knitweb.md`: this
  sentence *explains why we retired the name*. Keep the historical reference to the external
  *Loom Network* (or delete the sentence) — don't turn it into "Knitweb Network".
- **`docs/migration/PR_CHANGELOG.md` and this runbook**: historical records; leave their
  `loom` mentions intact (they describe the past).

## 3. Verification gate (all must pass before opening PR #2)

```bash
# (a) no stray loom identifiers/paths left in code (docs carve-outs excepted)
git grep -in loom -- 'src/*' 'tests/*' 'pyproject.toml'      # EXPECT: empty

# (b) CID INVARIANT — a sample signed record's cid is byte-identical pre/post rename.
#     Capture BEFORE the rename, compare AFTER:
PYTHONPATH=src python3 - <<'PY'
from knitweb.looms.chemistry import ChemistryLoom   # pre-rename import; post-rename: knitweb.knitwebs.chemistry import ChemistryKnitweb
# build one representative reaction-knowledge record + one Knit, print .cid
# (use the test fixtures in tests/knitwebs/test_chemistry.py); assert the hex matches the
# value recorded before the rename. ANY difference = a signed-byte regression -> STOP.
PY

# (c) full suite green
PYTHONPATH=src python3 -m pytest -q                          # EXPECT: ~255 passed

# (d) CLI entrypoint still resolves (the rename must not break app.cli)
pip install -e . >/dev/null && knitweb --help >/dev/null && echo "CLI OK"
```

The cleanest CID proof: before step 1, run the existing `tests/looms/*` once and record the
`cid` of a fixture record; after the rename, the renamed `tests/knitwebs/*` must produce the
identical `cid`. Because the rename never touches `to_record()` bytes, they will match — this
gate just *proves* it rather than trusting it.

## 4. PR #2 description checklist
- [ ] file/dir renames via `git mv` (history preserved)
- [ ] symbol/path sweep applied in the documented order
- [ ] `pyproject.toml` marker renamed + registered
- [ ] brand-collision carve-out preserved
- [ ] gate (a) empty, (b) CID identical (paste the hex), (c) suite green count, (d) CLI OK
- [ ] note the accepted overload: `knitweb.ledger.knitweb.Knitweb`

## 5. Affected files (inventory snapshot from `main`, for review scope)

Source: `ledger/loom.py` (→`ledger/knitweb.py`), `looms/{__init__,chemistry,finance,operational,supplychain}`
(→`knitwebs/…`), `ledger/{node,knit}.py`, `p2p/node.py`, `fabric/web.py`, `sdk/__init__.py`,
`__init__.py`. Tests: `tests/looms/test_{operational,finance,chemistry,supplychain}.py`
(→`tests/knitwebs/…`), `tests/property/{test_ledger,test_network_replay}.py`. Docs:
`docs/research/08-knitweb.md`, `SYNAPTIC_WEB.md`, `ROADMAP.md`, `IDENTITY_AND_ACCOUNTS.md`,
`PROOF_OF_USEFUL_WORK.md`, `research/README.md`. Config: `pyproject.toml` (the `loom` marker).
