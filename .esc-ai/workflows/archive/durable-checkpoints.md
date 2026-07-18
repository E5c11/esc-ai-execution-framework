# Capability 9 — Durable Checkpoints

**Status:** Complete

## Outcome

Allow another engineer or agent to resume unfinished work from bounded committed state
without reading a run transcript or reconstructing prior decisions.

## Completed

- [x] Canonical `workflows/active/<task-id>/checkpoint.yaml` discovery
- [x] Repository-relative task and artifact references
- [x] Safe task IDs and bounded progress entries
- [x] Create-without-overwrite behavior
- [x] Append/deduplicate updates with invalid-update rollback
- [x] Blocked and ready-to-resume consistency rules
- [x] Compact JSON inspection for AI consumers
- [x] Explicit review-and-commit reminder
- [x] Orchestrator failed-run checkpoint candidates
- [x] API retrieval without automatic repository mutation

## Constraints retained

- Checkpoints contain durable findings and decisions, not transcripts or secrets.
- Runtime candidates remain transient until deliberately promoted.
- Completed tasks do not retain active checkpoints.
