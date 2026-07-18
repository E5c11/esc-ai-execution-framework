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
- [ ] Rename `workflows/` -> `.esc-ai/workflows/` in this repo. Update
      `esc_exec/checkpoints.py`'s `CHECKPOINT_ROOT`, `tests/test_checkpoints.py`'s
      fixture paths, `INSTRUCTIONS.md`, and the `guides/durable-checkpoints.md` /
      `guides/execution-contracts.md` examples.
- [ ] Add `ecosystems:` to `schemas/route-registry.schema.yaml` and `registry.py`,
      validated so every member ID is itself a registered repository.
- [ ] Resolve `.esc-ai/runs/<run-id>/` per task inside `OpenCodeAdapter.execute`
      instead of a single caller-supplied `output_root`.
- [ ] Define a versioned framework descriptor
      (`schemas/framework-descriptor.schema.yaml` + `esc-framework.yaml` at this
      repo's and the architecture framework's root) and compatible-major-version
      compatibility checking.
- [ ] Extend repository/component manifests with an architecture selector
      (doc/profile ID), not just the current bare `frameworks: {id: version}` map.
- [ ] Add an architecture-document lookup module reading the architecture
      framework's `index.json` directly (data contract, no code dependency between
      the two repos).
- [ ] Extend `build_task_context` and `schemas/task-context.schema.json` with a
      resolved `architecture` section.
- [ ] Instruction-precedence thin slice (ordering only, one known conflict case).

## Decisions

See the plan doc's "Decisions required before implementation" section —
compatibility model, `.esc-ai/` placement, and ecosystem grouping are already
decided there.
