# Capability 7 — Task Context and Progressive Verification

**Status:** Complete

## Outcome

Give each run bounded repository routing context and a deterministic verification
sequence without embedding source, reports, or provider-specific execution concepts.

## Completed

- [x] Portable task-context and verification-plan schemas
- [x] Context generation from task scope and current repository/component indexes
- [x] Explicit component, path, and reference bounds with no silent truncation
- [x] Component-owned verification-profile schema and Gradle profile generation
- [x] Fixed focused → component → impact → final gate order
- [x] Required-input and not-applicable gate states
- [x] Deterministic multi-component check merging and deduplication
- [x] OpenCode prompt uses generated context
- [x] Orchestrator generates plans before execution and exposes both artifacts
- [x] Real `ampm-backend/content` adoption and validation

## Deferred by design

- Gate command execution belongs to a verification runtime.
- Focused test selection requires task evidence rather than guessed test names.
- Generated consumer checks require the dependency graph in Capability 10.
