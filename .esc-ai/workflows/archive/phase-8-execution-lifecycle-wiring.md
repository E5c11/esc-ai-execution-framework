# Phase 8 — Execution-Framework Wiring
**Status:** Complete
**Roadmap capability:** 19
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`
(Phase 8, "Integrated execution lifecycle") for full rationale; this tracks only this
repo's share. The bulk of Phase 8 (scheduler wiring, approval gates, retry, checkpoint
promotion, resume views, non-execution metrics persistence) is `esc-ai-orchestrator`'s
own tracking doc.

## Objective

Two Phase 8 bullets that belong in this repo specifically: "Resolve architecture
instructions into bounded task context" and "Retain metrics for onboarding, planning,
execution, and rework" (the execution side of metrics already existed; onboarding and
planning didn't).

## What changed

- `esc_exec/instructions.py`'s `order_instruction_bundle`/`check_extension_namespace_conflict`
  (built in Phase 1, never called by anything real since) are now wired into
  `OpenCodeAdapter.execute` via a new `_instruction_bundle` method. It composes the
  plan's six precedence levels — the run's policy id, the repository's declared
  execution-framework version, the task context's already-resolved
  `architecture.documents` per component, the repository's `.esc-ai/workflows/README.md`
  (and its `policy.extension` id, if declared) plus each component's manifest path, and
  the task's own id — and writes the ordered result to `instruction-bundle.json` in the
  run directory, referenced from `run.json.bindings.instruction_bundle` for audit, the
  same pattern already established for `tool_grant`.
- The extension-namespace-conflict check is wired in and *would* stop a run (`execute`
  raises before the run attempts anything) if a conflict were found — but it is
  currently always a no-op: `.esc-ai/workflows/README.md`'s `policy.extension`
  frontmatter (Phase 5) only declares an `id`/`precedence` note, not an enumerated list
  of document IDs, so there is nothing to check against yet. This is real, working
  wiring waiting on a declaration mechanism that doesn't exist yet, not a fabricated
  source pretending the check does something today.
- New `esc_exec/measurement.py::process_metrics(kind, process_id, created_at,
  updated_at, questions_asked, questions_answered)` — a minimal, comparable record for
  the *human-interaction* side of onboarding/planning (elapsed wall-clock time,
  questions asked vs. answered), deliberately not a dashboard or comparison engine —
  there's no real usage data yet to compare against. Registered as a new
  `process-metrics` contract kind (`schemas/process-metrics.schema.json`,
  `contracts.py`) with an example fixture, so it's validated the same way every other
  contract in this repo is. The orchestrator calls this with data already sitting in
  its `Store` (onboarding/plan draft and completion timestamps already recorded there).

## Verification

127/127 tests pass (122 prior + 5 new: two `test_opencode_adapter.py` tests confirming
the bundle is written, correctly ordered, and includes a workflow-policy extension
reference when one exists; three `test_measurement.py` tests for `process_metrics`'s
elapsed-time calculation, its unsupported-kind rejection, and a contract-validation
case where `questions.answered` exceeds `questions.asked`). No regressions to the
existing tool-grant/prompt-text behavior — all 11 prior `test_opencode_adapter.py`
tests still pass unchanged.

## Not built here (real, still open)

The extension-namespace-conflict check has no live data source, as noted above — it
will start doing something the moment a repository's workflow policy can declare
specific document IDs its extension defines, which isn't designed yet. No dashboard,
report, or cross-process comparison was built for `process_metrics` — deliberately, per
this project's standing discipline against building measurement tooling ahead of real
usage evidence.
