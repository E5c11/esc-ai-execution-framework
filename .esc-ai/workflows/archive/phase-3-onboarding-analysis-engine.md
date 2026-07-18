# Phase 3 — Onboarding Analysis Engine
**Status:** Complete
**Roadmap capability:** 14
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`
(Phase 3) for full rationale; this tracks only this repo's share (the orchestrator adds
statefulness on top — see its own tracking doc).

## Objective

`esc-exec repository analyze <path>` produces a complete onboarding proposal without
modifying the target repository, satisfying Phase 3's exit criterion ahead of the
unified `escape-ai` CLI (Phase 6).

## Deliverables

- [x] Generalized build-system adapter interface: `esc_exec/adapters.py`'s
      `BuildSystemAdapter` Protocol (`detects`/`detect`), a `GradleAdapter` wrapping the
      existing `detect_gradle_repository`, and `detect_build_system()` dispatching
      across a registered adapter list — one concrete adapter today, extensible later.
- [x] `esc_exec/onboarding.py`'s `analyze_repository()`: strictly read-only, classifies
      the repository manifest and each detected component's manifest as
      create/update/preserve/deprecate against detected structure, with evidence.
      Surfaces a semantic question for any component missing an authored `purpose`.
      Detects `INSTRUCTIONS.md`, `.esc-ai/workflows/`, and `context/project-profile.yaml`
      presence as existing-adoption signals. Computes a stable `input_digest` (sha256
      over adapter name, repository id, and sorted component id/path pairs) so repeated
      analysis against unchanged inputs is verifiably idempotent.
- [x] New `schemas/onboarding-proposal.schema.json`; registered as the
      `onboarding-proposal` contract kind in `contracts.py` (`CONTRACT_FORMATS`,
      `REQUIRED`, and a kind-specific action/evidence check), with an example fixture
      under `examples/contracts/`.
- [x] `esc-exec repository analyze <path> [--output PATH]` CLI command. Verified
      read-only both in tests (file-listing snapshot before/after) and manually against
      a real temp Gradle repo — the proposal correctly names `esc-execution.yaml` and
      the component manifest as `create`, and neither is actually written.

## Test coverage

10 new tests: `tests/test_adapters.py` (detection + the no-adapter-found error) and
`tests/test_onboarding.py` (fresh-repository create/questions, no-write guarantee,
preserve after generation + authored purpose, component-set-change → update, removed
component → deprecate, digest stability/change, existing-adoption detection, contract
validity). Plus one existing test (`test_contracts.py`'s connected-examples check)
extended for the new contract kind. 84/84 tests pass overall.
