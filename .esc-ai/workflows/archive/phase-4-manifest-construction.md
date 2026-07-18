# Phase 4: Human-Assisted Manifest/Profile Construction
**Status:** Complete
**Roadmap capability:** 15
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`,
Phase 4, for full rationale; this tracks only this repo's share (Part B of a
three-repo round -- Part A was `esc-ai-architecture-framework`'s
`profile-doc-map.json` export, Part C is `esc-ai-orchestrator`'s answer/apply
HTTP endpoint).

## Delivered

- `esc_exec/architecture_lookup.py`: `load_profile_doc_map` (reads the architecture
  framework's `profile-doc-map.json` as data, no code coupling) and
  `suggest_profile_ids` (mirrors `tools/lookup.py::profile_extra_docs`).
- `esc_exec/onboarding.py`:
  - `import_project_profile` reads a legacy `context/project-profile.yaml` if present.
  - `analyze_repository` gained an optional `registry_path` and now returns
    `profile_id_suggestions`; components with an importable project profile get a
    repository-wide suggestion and skip the question entirely; components with
    neither an existing `architecture.profile_ids` nor an importable profile get one
    bounded semantic question (frameworks/targets), not a question per possible field.
  - `apply_onboarding_answers` is the first onboarding step that writes to the
    repository (`analyze_repository` stays strictly read-only). It generates baseline
    manifests, merges purpose/profile_id answers non-destructively, regenerates
    indexes, then auto-generates verification/architecture profiles for any component
    that lacks one. Rejects a stale proposal (repository changed since analysis)
    rather than silently applying against a different structure.
  - Stub documents and empty profile_id suggestions are surfaced in the result, never
    silently treated as complete.
- `onboarding-proposal.schema.json` gained an optional `profile_id_suggestions` field.
- CLI: `esc-exec repository answer <path> <proposal> <answers>`.

## Verification

- 98 tests pass (14 new: 6 in `test_architecture_lookup.py`, 8 in `test_onboarding.py`).
- Manually verified end-to-end: `repository analyze` -> `repository answer` on a real
  temp Gradle repository produced a valid `esc-component.yaml` with authored
  `purpose`, plus auto-generated `esc-verification-profile.yaml` and
  `esc-architecture-profile.yaml`; both manifests pass `manifest validate`.

## Known scope limit

Manifest *writing* (`generate_gradle_manifests`, called at the top of
`apply_onboarding_answers`) is still Gradle-specific, unlike detection
(`detect_build_system`, generalized in Phase 3). Not a regression -- Gradle remains
the only adapter -- but worth flagging before a second build-system adapter is added.
