# Migratieplan — `--mirror` break-out van modules met meerdere commands

> **Repo:** `febuz/pulse` (Knitweb) → org `knitweb`, repo-naam **`pulse` behouden**
> **Cutover:** **18 juni 2026, 21:00** · **Status:** plan (nog niet uitgevoerd)
> **Principe:** full-fidelity migratie via `git clone --mirror` / `git push --mirror`; per command-dragende module een history-behoudende break-out.
> **Aansluiting:** dit plan is **PR #3 / fase na** de bestaande migratie-handoff (zie §1bis); het draait ná de org-mirror, PR #1 consistency-pass en PR #2 `loom→knitweb`.

## 1bis. Aansluiting op de bestaande migratie (alignment)

Dit plan staat **niet los**: het is de vervolgstap op de handoff in `docs/migration/` (`MIGRATION.md`, `MIGRATION_PLAN.md`, `RENAME_RUNBOOK.md`, `CONTINUATION.md`, `PR_CHANGELOG.md`).

**Canonieke doelnaam (let op — opgelost):** de oudere handoff-docs noemen nog `github.com/knitweb/**knitweb**` (org = repo = package). Dat is **achterhaald**: de huidige `pyproject.toml` `[project.urls]` én `CHANGELOG.md` zeggen *"home = `github.com/knitweb/**pulse**`, de repo-naam `pulse` blijft behouden, package = `knitweb`"*. **Dit plan volgt de actuele beslissing: `knitweb/pulse`.** De `knitweb/knitweb`-verwijzingen in de oude docs zijn een openstaande inconsistentie (zie `INCONSISTENCY_FINDINGS`).

**Volgorde (deze break-out komt ná):**
1. **Org-mirror** — `febuz/pulse` → `knitweb/pulse` via `git push --mirror` (handoff Fase 2).
2. **PR #1 — consistency-pass** (branch `fix/consistency-pass-02`; pass-01 is geland).
3. **PR #2 — `loom→knitweb` rename** (`RENAME_RUNBOOK.md`): `ledger/loom.py → ledger/knitweb.py`, `looms/ → knitwebs/`, `*Loom → *Knitweb`. **Signed-record CID-invariant blijft** (alleen identifiers/paden/prose).
4. **PR #3 — deze `--mirror` break-out** (hieronder).

> **Padconsequentie:** omdat PR #2 vóór deze break-out landt, gebruiken de `filter-repo`-paden in §7 de **post-rename** namen waar van toepassing (`ledger/` blijft `ledger/`, maar `looms/`→`knitwebs/`; de ledger-break-out raakt `ledger/loom.py`→`ledger/knitweb.py`). Draait de break-out tóch vóór PR #2, gebruik dan de oude paden en herhaal de rename per nieuwe repo.

---

## 1. Doel & scope

De monorepo `febuz/pulse` bevat één `knitweb`-CLI die **acht subcommands** dispatcht over meerdere modules. Dit plan breekt de modules **met méér dan één command** uit naar eigen repo's onder de `knitweb`-org — met behoud van git-historie — via het `--mirror`-mechanisme. De protocol-/geldkern blijft in `knitweb/pulse` (conform `docs/IDENTITY_AND_ACCOUNTS.md`: *"Build under `knitweb`. Reserve `pulse` and `fiber`."*).

**In scope:** org-migratie van de hele repo; uitsplitsen van de twee multi-command modulegroepen; CLI-hersplitsing; verificatie + rollback.
**Niet in scope:** functionele refactor, token-foundation-splitsing (pas bij governance-trigger), CI-herinrichting (aparte taak).

---

## 2. Uitgangssituatie — module → command map

Bron: `src/knitweb/app/cli.py` (`add_subparsers(dest="cmd")`), imports `sdk`, `store`, `edge.runtime`, `ledger.node`, `p2p.node`.

| Command | Backing module | Groep | # commands in groep |
|---|---|---|---|
| `wallet` | `ledger` (+ `sdk`, `store`) | **Ledger/wallet** | **4** |
| `address` | `ledger` | **Ledger/wallet** | |
| `balance` | `ledger` | **Ledger/wallet** | |
| `pay` | `ledger` + `p2p` | **Ledger/wallet** | |
| `node` | `p2p` | Protocol-kern | 1 |
| `compile` | `synaptic` (+ `anchor`) | **Synaptic/edge** | **3** |
| `verify-bundle` | `synaptic` / `edge` | **Synaptic/edge** | |
| `edge-load` | `edge.runtime` | **Synaptic/edge** | |

**Multi-command modules (= break-out targets):** Ledger/wallet (4) en Synaptic/edge (3).
**Single-command (blijft in kern):** `node` (p2p).

Repo-feiten (peilmoment): remote `git@github.com:febuz/pulse.git`, branch `fix/consistency-pass-02`, **83 commits**, **0 tags**, **1 branch**. LOC per module: core 604 · ledger 507 · p2p 616 · fabric 901 · pouw 792 · looms 695 · token 200 · anchor 234 · synaptic 320 · edge 190 · sdk 95 · app 292.

---

## 3. Doelarchitectuur (repo's onder `knitweb`)

| Nieuwe repo | Inhoud | CLI-entrypoint | Commands |
|---|---|---|---|
| `knitweb/pulse` | L0 core, L1 ledger-engine, L2 p2p, L3 fabric, L4 pouw, L5 looms, L6 token — de protocol-/geldkern | `knitweb` | `node` (+ meta-dispatch) |
| `knitweb/pls-wallet` | `ledger` wallet-laag + `sdk` + `store` als gebruikers-tooling | `pls` | `wallet · address · balance · pay` |
| `knitweb/fiber-edge` | `synaptic` (Fiber Synaptic Compiler) + `edge` runtime + `anchor` | `fiber` | `compile · verify-bundle · edge-load` |

Gedeelde code (`core`, canonical CBOR, crypto) wordt **niet gekopieerd** maar als dependency gepubliceerd: de break-out repo's hangen aan `knitweb-core` (een wheel uit `knitweb/pulse`), niet aan een gedupliceerde boom. Dit voorkomt drift.

---

## 4. Waarom `--mirror`

`git clone --mirror` maakt een **bare** repo die *alle* refs spiegelt (heads, tags, notes, remote-tracking) en `git push --mirror` schrijft die 1-op-1 naar een doel. Dit is het juiste primitief voor migratie:

- **Volledigheid** — geen verlies van branches/tags/notes (een gewone `clone` pakt alleen de default branch volledig).
- **Idempotent & verifieerbaar** — bron en doel zijn ref-voor-ref te diffen.
- **Veilig startpunt voor extractie** — `git filter-repo` herschrijft historie destructief; je draait dat *altijd* op een wegwerp-`--mirror`-kloon, nooit op je werkkopie.

> ⚠️ `git push --mirror` **verwijdert** refs op het doel die niet in de bron staan. Alleen op een *leeg/nieuw* doel-repo gebruiken, nooit op een repo met eigen werk.

---

## 5. Fase 0 — Voorbereiding (geen herschrijving)

```bash
# 0.1  Werkmap voor de migratie
mkdir -p ~/migrate-knitweb && cd ~/migrate-knitweb

# 0.2  Full-fidelity veiligheidsspiegel (DE bron-of-truth voor alle volgende stappen)
git clone --mirror git@github.com:febuz/pulse.git pulse-mirror.git

# 0.3  Inventaris vastleggen (voor de diff achteraf)
cd pulse-mirror.git
git show-ref | tee ~/migrate-knitweb/REFS_BEFORE.txt
git rev-list --count --all                       # commit-telling vastleggen
pip install git-filter-repo                       # extractie-tool (eenmalig)
cd ..
```

**Freeze-afspraak:** vanaf 0.2 geen merges naar `febuz/pulse` tot de migratie klaar is. **Cutover-venster: 18 juni 2026, 21:00** — ververs de mirror (`git clone --mirror`) vlak vóór 21:00 zodat de laatste commits meegaan, en kondig de switch naar `knitweb/pulse` aan alle agents/CI aan (anders splitst werk over twee repo's).

---

## 6. Fase 1 — Hele repo naar de org via `--mirror`

Lege doel-repo `knitweb/pulse` eerst aanmaken (UI of `gh repo create knitweb/pulse --private`).

```bash
cd ~/migrate-knitweb/pulse-mirror.git
git remote set-url --push origin git@github.com:knitweb/pulse.git
git push --mirror                                 # alle 83 commits + branch + (0) tags
# Verifieer: refs op doel == REFS_BEFORE.txt
git ls-remote git@github.com:knitweb/pulse.git | sort > ~/migrate-knitweb/REFS_AFTER.txt
```

`knitweb/pulse` is nu de protocol-kern. De break-outs in Fase 2 verwijderen later hun verhuisde modules hier (Fase 3.3).

---

## 7. Fase 2 — Break-out van de multi-command modules

Elke break-out volgt hetzelfde patroon: **verse `--mirror`-kloon → `filter-repo` op de module-paden → nieuwe remote → `push --mirror`.** `filter-repo` strip de oude `origin` (veiligheidsmaatregel), dus die zetten we opnieuw.

### 7a. Ledger/wallet → `knitweb/pls-wallet` (4 commands)

```bash
cd ~/migrate-knitweb
git clone --mirror git@github.com:febuz/pulse.git pls-wallet.git
cd pls-wallet.git

# Alleen de wallet-dragende paden behouden; historie van die paden blijft intact
git filter-repo --force \
  --path src/knitweb/ledger/ \
  --path src/knitweb/sdk/ \
  --path src/knitweb/store.py \
  --path src/knitweb/app/cli.py \
  --path-rename src/knitweb/:src/pls_wallet/

gh repo create knitweb/pls-wallet --private
git remote add origin git@github.com:knitweb/pls-wallet.git
git push --mirror origin
```

### 7b. Synaptic/edge → `knitweb/fiber-edge` (3 commands)

```bash
cd ~/migrate-knitweb
git clone --mirror git@github.com:febuz/pulse.git fiber-edge.git
cd fiber-edge.git

git filter-repo --force \
  --path src/knitweb/synaptic/ \
  --path src/knitweb/edge/ \
  --path src/knitweb/anchor/ \
  --path src/knitweb/app/cli.py \
  --path-rename src/knitweb/:src/fiber_edge/

gh repo create knitweb/fiber-edge --private
git remote add origin git@github.com:knitweb/fiber-edge.git
git push --mirror origin
```

> De gekopieerde `cli.py` is bewust meegenomen als **historie-anker**; in Fase 3 wordt hij teruggesnoeid tot alleen de commands van die groep.

---

## 8. Fase 3 — CLI hersplitsing & ontdubbeling

De ene `knitweb`-dispatcher wordt drie command-groepen. Per repo één entrypoint in `pyproject.toml`:

| Repo | `[project.scripts]` | Backt op |
|---|---|---|
| `knitweb/pulse` | `knitweb = "knitweb.app.cli:main"` | alleen `node` + meta |
| `knitweb/pls-wallet` | `pls = "pls_wallet.cli:main"` | `wallet·address·balance·pay` |
| `knitweb/fiber-edge` | `fiber = "fiber_edge.cli:main"` | `compile·verify-bundle·edge-load` |

**Stappen:**
1. **3.1** In elke break-out: `cli.py` snoeien tot de eigen subparsers; importpaden `knitweb.*` → `pls_wallet.*` / `fiber_edge.*`; gedeelde core toevoegen als dependency `knitweb-core>=0.6` (gepubliceerd vanuit `knitweb/pulse`).
2. **3.2** In `knitweb/pulse`: `pls-mint`/wallet/bytecode-subparsers uit `app/cli.py` halen; `knitweb` houdt `node` + een dunne meta-dispatch die naar `pls`/`fiber` doorverwijst indien geïnstalleerd.
3. **3.3** In `knitweb/pulse` de verhuisde mappen verwijderen (`git rm -r src/knitweb/synaptic src/knitweb/edge src/knitweb/anchor` enz. — alléén nadat 7a/7b geverifieerd zijn), met `docs/` cross-refs bijwerken.
4. **3.4** Property-tests per repo groen: `PYTHONPATH=src python3 -m pytest tests/property -q`.

---

## 9. Fase 4 — Verificatie & rollback

**Verificatie (per repo):**
```bash
# Historie van een module daadwerkelijk meegekomen?
git -C pls-wallet.git log --oneline -- src/pls_wallet/ledger | wc -l
# Geen blobs verloren / repo gezond?
git -C pls-wallet.git fsck --full
# CLI werkt
pip install -e . && pls --help && fiber --help
```

**Rollback:** niets is destructief op de bron. `febuz/pulse` blijft ongewijzigd en `pulse-mirror.git` (Fase 0.2) is een volledige kopie. Bij twijfel: nieuwe org-repo's verwijderen, `febuz/pulse` opnieuw spiegelen, herstarten. Fase 3.3 (`git rm` in de kern) pas mergen ná akkoord op de break-outs.

---

## 10. Risico's & checklist

| Risico | Mitigatie |
|---|---|
| `push --mirror` overschrijft een niet-leeg doel | Alleen op vers aangemaakte org-repo's draaien |
| `filter-repo` op de werkkopie i.p.v. wegwerp-kloon | Altijd starten vanaf een verse `git clone --mirror` |
| Gedeelde `core`/crypto gedupliceerd → drift | Als `knitweb-core` wheel publiceren, niet kopiëren |
| Gebroken imports na rename | `--path-rename` + zoek-vervang `knitweb.` → nieuw pakket; tests groen vereist |
| Verloren branches/tags | `REFS_BEFORE.txt` vs `ls-remote` diffen na elke push |
| `pay` raakt zowel ledger als p2p | `pls pay` praat via SDK met een draaiende `knitweb node`; geen p2p-code kopiëren |

**Go/No-go checklist:** ☐ 0.2 mirror gemaakt ☐ REFS_BEFORE vastgelegd ☐ org-repo's leeg aangemaakt ☐ Fase 1 refs-diff schoon ☐ 7a/7b `fsck` schoon ☐ tests groen per repo ☐ CLI's `--help` werken ☐ pas dan Fase 3.3 (`git rm`) mergen.

---

## 11. Bijlage — command-cheatsheet

```bash
# Volledige spiegel (backup + org-migratie)
git clone --mirror <src>            #  bare repo met alle refs
git push   --mirror <dest>          #  alle refs 1-op-1 naar leeg doel

# History-behoudende module-extractie (op een wegwerp-mirror)
git filter-repo --force --path <dir>/ --path-rename src/knitweb/:src/<pkg>/
git remote add origin <dest> && git push --mirror origin

# Verificatie
git show-ref                        #  refs vastleggen
git fsck --full                     #  integriteit
git ls-remote <dest>                #  refs op doel
```
