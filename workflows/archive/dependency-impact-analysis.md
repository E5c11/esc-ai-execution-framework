# Capability 10 — Dependency Graph and Impact Analysis

**Status:** Complete

## Outcome

Generate consumer relationships from build evidence and use them to select progressive
impact verification without broad repository-wide guessing.

## Completed

- [x] Canonical `esc-dependencies.json` schema and generation
- [x] Gradle `project(":path")` dependency extraction
- [x] Deterministic nodes and consumer-to-dependency edges
- [x] Build-input digest and stale graph detection
- [x] Direct and transitive consumer analysis
- [x] Portable bounded impact-analysis JSON
- [x] Verification-plan impact metadata
- [x] Consumer component checks populate the impact gate
- [x] Missing consumer profiles stop with a generation request
- [x] Real `content` → `recommendations` pilot in `ampm-backend`

## Deferred by design

- Type-safe Gradle project accessors need a dedicated parser adapter.
- Cross-repository dependencies require registry-aware graph federation.
- The undeclared root application remains covered by the final repository gate.
