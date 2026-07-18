# Escape AI — Phase 1: Framework Composition Protocol
**Status:** In progress
**Roadmap capability:** 12
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`
(Phase 0 + Phase 1) for full rationale; this tracks only this repo's share.

## Objective

Give a task context the ability to identify the exact execution and architecture
documents to load without hard-coded checkout paths, and give generated run
artifacts an obvious, repository-local home instead of a process-relative default.

## Deliverables

- [x] Migration diagnostic for the renamed architecture-framework ID
      (`RENAMED_FRAMEWORK_IDS` in `registry.py`; wired into `resolve_route`,
      `validate_registry`, and `validate_repository`).
- [x] Rename `workflows/` -> `.esc-ai/workflows/` in this repo. Updated
      `esc_exec/checkpoints.py`'s `CHECKPOINT_ROOT`, `tests/test_checkpoints.py`'s
      fixture paths, `INSTRUCTIONS.md`, and the `guides/durable-checkpoints.md` /
      `guides/execution-contracts.md` examples. The `.execution/runs/...` mentions in
      those same guides are left for the output-root deliverable below, since they
      describe the thing that deliverable changes.
- [x] Add `ecosystems:` to `schemas/route-registry.schema.yaml` and `registry.py`
      (`add_ecosystem`, validated in `validate_registry` against registered
      repository IDs). `esc-exec route ecosystem add <name> <repo-id>...` and
      `route list` expose it from the CLI.
- [x] Resolve `.esc-ai/runs/<run-id>/` per task inside `OpenCodeAdapter.execute`,
      which now derives `run_dir` from the resolved repository instead of taking a
      caller-supplied `output_root` (removed from `execute()`, the CLI's `opencode
      execute --output` flag, and both test call sites). `.esc-ai/runs/` added to
      `.gitignore` (kept alongside `.execution/`, which other guides still use as an
      example scratch path for unrelated commands). Follow-up in
      `esc-ai-orchestrator`'s own tracking doc changes the `Runtime` protocol/
      `Scheduler` there to match.
- [x] Define a versioned framework descriptor
      (`schemas/framework-descriptor.schema.yaml` + `esc-framework.yaml` at this
      repo's and the architecture framework's root) and compatible-major-version
      compatibility checking. `esc_exec/framework_descriptor.py`'s
      `check_framework_compatibility` resolves each declared `frameworks.{id}` major
      version against the checked-out framework's own `esc-framework.yaml`, wired into
      `validate_repository` via an optional `registry_path` parameter (skipped when
      not provided, so existing callers/tests are unaffected). `manifest validate`'s
      CLI command now passes the registry through. Also registered as a
      `framework-descriptor` contract kind in `contracts.py` for CLI parity.
- [x] Extend repository/component manifests with an architecture selector
      (doc/profile ID), not just the current bare `frameworks: {id: version}` map.
      Both `repository-manifest.schema.yaml` and `component-manifest.schema.yaml` gained
      an optional `architecture: {profile_ids: [doc-id, ...]}` — repository-level sets
      the default, component-level overrides/extends it. `manifests.py` validates only
      the field's shape (non-empty list of non-empty strings); resolving whether the
      IDs actually exist in the architecture framework's index is the task-context
      integration deliverable below, not this one.
- [x] Add an architecture-document lookup module reading the architecture
      framework's `index.json` directly (data contract, no code dependency between
      the two repos). `esc_exec/architecture_lookup.py`: `load_architecture_index`
      reads the index; `resolve_architecture_docs` topologically resolves one or more
      seed doc IDs via `requires`, merging multi-seed results by layer order rather
      than forcing every seed to the end (that only made sense for a single seed);
      `stub_documents` flags `status: stub` entries for the Gap Protocol rather than
      treating them as fully specified. Missing doc IDs are reported back, never
      silently dropped.
- [x] Extend `build_task_context` and `schemas/task-context.schema.json` with a
      resolved `architecture` section. `build_task_context` gained an optional
      `registry_path` parameter (mirroring `validate_repository`'s pattern); for each
      selected component it unions the repository's and component's
      `architecture.profile_ids`, resolves them via `architecture_lookup`, and attaches
      `{profile_ids, documents, missing?, stubs?}` under that component's entry in
      `routing.components`. Raises if a component declares `profile_ids` but no
      `registry_path` was given — that combination can't be resolved, so it fails loud
      rather than silently omitting the section. `task-context.schema.json`'s
      `routing.components` gained a proper `items` schema (it was untyped before).
      Wired into the two real call sites: `OpenCodeAdapter.execute` (via
      `self.registry_path`) and the CLI's `context build` command (via `registry`).
- [ ] Instruction-precedence thin slice (ordering only, one known conflict case).

## Decisions

See the plan doc's "Decisions required before implementation" section —
compatibility model, `.esc-ai/` placement, and ecosystem grouping are already
decided there.
