# Legacy Materials

This folder contains historical Knitweb material kept for reference during the Pulse migration.

Legacy material is not the current Pulse architecture. Current Pulse work lives in `apps/`,
`peer/`, `packages/`, `services/`, `specs/`, and `worlds/`.

## Imports

- `weaving-app/`: the old Knitweb pattern editor and machine-facing app, imported without dependencies.

## Rules

- Do not build new Pulse architecture inside `legacy/`.
- Do not depend on vendored dependency folders from old projects.
- Use legacy code as product history, interaction reference, and migration source material.
