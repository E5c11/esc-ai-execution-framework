# Capability 11 — Execution-Efficiency Measurement

**Status:** Complete — empirical provider cohort pending

## Outcome

Capture comparable execution evidence and calculate improvements or regressions
without estimating missing provider data.

## Completed

- [x] Portable run-metrics and efficiency-comparison schemas
- [x] Context-size, elapsed-time, tool-call, read-call, and rework dimensions
- [x] OpenCode provider token extraction including cache fields
- [x] Explicit unavailable-token representation
- [x] Metrics emitted for successful and failed adapter runs
- [x] Baseline/candidate cohort averages and sample counts
- [x] Savings/regression percentage calculation
- [x] Orchestrator metrics retrieval endpoint
- [x] Experimental comparison guidance

## Evidence status

- Deterministic fixtures prove collection, validation, and comparison behavior.
- Real savings remain unclaimed until a working provider supplies comparable runs.
- The earlier local OpenCode model limitation still prevents a representative cohort.
