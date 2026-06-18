<!-- Thanks for contributing to Knitweb! Keep PRs small and focused. -->

## Summary

<!-- What does this change and why? Link the issue it closes: "Closes #123". -->

## Type

- [ ] Bug fix
- [ ] Feature
- [ ] Docs
- [ ] Refactor / internal (no behaviour change)

## Proofs

<!-- Paste the green test output. New behaviour must add a property test. -->

```
PYTHONPATH=src python3 -m pytest -q
```

## Checklist (see CONTRIBUTING.md)

- [ ] Tests are **green** and new behaviour has a runnable proof.
- [ ] **No signed-record byte change** — no `kind`/field/value altered; sample record CIDs/signatures are byte-identical (or this PR is explicitly a canonical-format change and says so).
- [ ] **No floats** on the hashing/balance/canonical path; money & state stay integers.
- [ ] **Vocabulary**: web · knitweb · knit · pulse · fiber. No "loom"/"looms" (only *knitweb*); no "network"/"net" except the `network` id field; PLS is the pay-token, FBR reserved.
- [ ] Crypto stays secp256k1 ECDSA + SHA-256; no founder premine introduced.
- [ ] Docs updated if behaviour/structure changed; PR is focused and not on `main`.
- [ ] Commits signed off (`-s`, DCO).
