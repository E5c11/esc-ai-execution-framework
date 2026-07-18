# Capability 6 — Compact Verification Reports

**Status:** Complete

## Outcome

Provide a deterministic, bounded first read for test results while retaining the full
tool report for diagnosis.

## Completed

- [x] Portable `verification-summary` JSON schema and validation
- [x] Component-owned report profile with explicit output bounds
- [x] JUnit XML summarizer and CLI
- [x] Failure omission counts and workspace-relative full-report reference
- [x] Orchestrator summary retrieval endpoint
- [x] Tests for aggregation, bounds, validation, and HTTP retrieval

## Constraints retained

- Complete reports remain authoritative and are not committed by default.
- Summaries are generated artifacts, not parallel human-authored reports.
- Missing component profiles do not silently fall back to an implicit global profile.
- Additional report formats must use adapters without changing the portable summary
  principle.
