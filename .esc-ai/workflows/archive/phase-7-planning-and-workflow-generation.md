# Phase 7 — Feature/Fix Planning and Workflow Generation
**Status:** Complete (typed-question scope; multi-turn conversation deliberately deferred)
**Plan:** [`../../../plan/cohesive-system-integration-and-onboarding.md`](../../../plan/cohesive-system-integration-and-onboarding.md) (Phase 7)
**Spans:** esc-ai-execution-framework (this doc) and esc-ai-orchestrator (its own tracking doc)

## Objective

Turn an objective and repository selection into approved, independently resumable
workflow files — one `task.yaml`/`README.md` per repository, cross-linked by stable
repository/task IDs for multi-repository initiatives — without requiring the
multi-turn, runtime-mediated planning conversation the plan describes.

## Deliberate scope boundary

The plan's "Planning conversation" section describes a live, policy-enforced,
runtime-mediated conversation for open-ended product judgment. That mechanism is
explicitly gated on the policy-to-tool-grant enforcement gap (flagged since Phase 1).
Building a "bounded conversation" on top of an unenforced permission model would be
the same false-safety problem this project has been careful to avoid elsewhere, so
this phase does not build it. Instead, it reuses the same typed-question-then-
human-answer pattern that already works for onboarding (Phases 3-6): a bounded set of
questions covering exactly what the plan says can't be derived (component selection,
scope boundary, completion conditions, rollout needs), answered once, non-
conversationally. Turning an objective into a task graph does not require a live AI
conversation — it requires structured decisions, which is what onboarding already
proved out. Upgrading this to a real mediated conversation remains future work, gated
on the enforcement fix landing.

## Deliverables

- [x] Initiative/task-graph contracts: `schemas/initiative.schema.yaml` (id,
      objective, work_type, tasks: `[{repository, task_id, depends_on?}]`) plus an
      optional `task.initiative: {id, depends_on}` extension on the existing
      `schemas/task-specification.schema.yaml`. Registered as an `initiative`
      contract kind in `contracts.py` (`CONTRACT_FORMATS`/`REQUIRED`/`ENUMS`), with a
      kind-specific check that every `depends_on` entry references another task
      declared in the same initiative document. Example fixture at
      `examples/contracts/initiative.yaml`.
- [x] `esc_exec/planning.py`:
      `route_objective` reuses `match_components` (no reimplemented matching);
      `planning_questions` produces one `components` question per repository plus
      three shared questions (`scope_boundary`, `completion_conditions`,
      `rollout_needs`) — the typed-question substitute for the deferred live
      conversation;
      `generate_single_repository_workflow` validates components resolve against
      the repository's own index (and the task ID is safe, and the work type is
      known, and completion conditions are non-empty) before writing anything, then
      writes `.esc-ai/workflows/active/<task-id>/task.yaml` + `README.md` (the
      README includes referenced `architecture.profile_ids` pulled from each
      component's manifest, when present);
      `generate_multi_repository_workflow` validates every referenced repository ID
      resolves, every task ID is safe, and every `depends_on` entry references
      another task in the same initiative — collecting *all* errors before writing
      a single file to *any* repository, so a bad reference in one repository never
      leaves a partial write in another.
- [x] Tests (`tests/test_planning.py`, 8 new): objective routing delegates to the
      real index-matching logic; single-repo generation produces a valid `task`
      contract and fails clean (nothing written) on an unresolvable component,
      unknown work type, or unsafe task ID; multi-repo generation validates
      everything up front (a bad reference in one repository blocks writes to
      every repository, not just the bad one) and produces correctly cross-linked
      `task.initiative` fields when validation passes.

## Verification

122 tests pass (114 prior + 8 new). One unrelated, pre-existing failure was observed
in `test_opencode_adapter.py` during a concurrent, in-flight change to
`opencode_adapter.py`'s policy-to-tool-grant wiring by another process — not part of
this phase, not touched by it, and isolated to files this phase's commit does not
include.
