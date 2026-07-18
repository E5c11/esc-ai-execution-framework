# Capability 8 — Executable Architecture Checks

**Status:** Complete

## Outcome

Turn mechanically enforceable architecture decisions into deterministic,
component-owned fitness functions with stable rule IDs and bounded reports.

## Completed

- [x] Architecture profile and report schemas
- [x] Canonical component-owned discovery convention
- [x] Profile generation that requests semantic rule authoring
- [x] Stable and unique rule-ID enforcement
- [x] Deterministic `forbidden-import`, `forbidden-path`, and `required-path` rules
- [x] Bounded violations and evidence with omission counts
- [x] CLI execution with pass, fail, and incomplete exit semantics
- [x] Portable report validation
- [x] Progressive component-gate integration
- [x] Real passing `content.no-portal-imports` pilot rule in `ampm-backend`

## Deferred by design

- Generated module dependency rules require Capability 10.
- Suppressions and baselines should be added only with explicit ownership and expiry.
- A general rule expression language is avoided until concrete adapter needs justify it.
