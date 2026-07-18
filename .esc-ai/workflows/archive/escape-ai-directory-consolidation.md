# `.esc-ai/` Generated-Artifact Consolidation
**Status:** Complete
**Roadmap capability:** 20
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`,
"Composition model" and "Repository-local Escape AI directory" sections (rewritten by
this capability) for the full reasoning; this tracks only this repo's share.

## Why

The plan previously argued the identity/discovery files (`esc-execution.yaml`,
`esc-index.json`, `esc-dependencies.json`, `<component>/esc-component.yaml`,
`INSTRUCTIONS.md`) needed to stay at repository/component root "for discoverability,
the same reason `package.json` isn't inside `.npm/`." That premise doesn't hold:
escape-ai never discovers a repository by scanning a directory tree for these files.
It always resolves a repository through the machine-local registry by ID first, then
reads a conventional relative path under an already-known root -- a hard non-goal
("the system must never scan arbitrary parent directories for repositories") already
ruled out the scenario the `package.json` analogy was defending against.

Real evidence from onboarding a representative 10-component repository showed the
actual cost of the old layout: every component directory got 4 escape-ai files mixed
in with its real source/build/README files.

## Delivered

- Every escape-ai-owned generated/managed file now lives under a repository-local
  `.esc-ai/` directory instead of scattered at repository root and inside each
  component's own source directory:
  - `.esc-ai/esc-execution.yaml` (repository manifest)
  - `.esc-ai/esc-index.json` (root routing index)
  - `.esc-ai/esc-dependencies.json` (dependency graph)
  - `.esc-ai/INSTRUCTIONS.md` (thin instruction pointer)
  - `.esc-ai/components/<component-id>/esc-component.yaml` (component manifest)
  - `.esc-ai/components/<component-id>/esc-index.json` (component index)
  - `.esc-ai/components/<component-id>/esc-verification-profile.yaml`
  - `.esc-ai/components/<component-id>/esc-architecture-profile.yaml`
- Per-component files are **flat and keyed by the component's stable ID**, not
  mirroring its filesystem path. Component IDs are the stable identifier this system
  already resolves everything else by; physical paths are expected to change
  (refactors, renames), so mirroring path would have just recreated the
  coupling-to-something-that-moves problem in a new location.
- `esc_exec/manifests.py` gained the shared `.esc-ai/` constants/helpers
  (`ESC_AI_DIR`, `COMPONENTS_DIR`, `repository_manifest_path`,
  `component_manifest_path`, `component_manifest_relative_path`,
  `repository_manifest_relative_path`) that every other module now uses instead of
  independently constructing the same path prefix.
- **Core principle held throughout:** `component["path"]` (the component's real
  source location) is completely unaffected by this refactor and always resolves as
  `repository_root / component["path"]`. The manifest's own storage location is a
  *different* thing (`repository_root / ".esc-ai" / "components" / component["id"]`).
  Every `paths.*` field describing the component's real source tree (`source`,
  `tests`, `resources`, `test_resources`, `migrations`, `documentation`, `build`)
  resolves relative to `component["path"]`; every `paths.*` field describing a file
  generated alongside the manifest itself (`verification_profile`,
  `architecture_profile`) stays relative to the manifest's own location, since those
  move together with it as one bundle.
- Fixed two latent bugs this divergence exposed, invisible before now because
  manifest-location and component-source-location happened to be identical:
  - `esc_exec/dependencies.py` `build_dependency_graph`: `build_path` now resolves as
    `repository / manifest["component"]["path"] / paths.build`, not
    `manifest_path.parent / paths.build` -- matching how `indexing.py`'s
    `build_component_index` already correctly used `component_root = root /
    component["path"]`.
  - `esc_exec/architecture.py` `check_architecture`: `component_root` now resolves as
    `repository / component["path"]` (the already-loaded index entry's `path` field),
    not `manifest_path.parent`.
- Both fixes have dedicated regression coverage: `tests/test_dependencies.py` and
  `tests/test_architecture.py` fixtures deliberately place each component's real
  source tree at `<root>/<component>/` while its manifest bundle lives at
  `.esc-ai/components/<component>/` -- a divergence that would make the fixed code
  paths fail immediately if either bug were reintroduced. Verified this directly by
  temporarily reverting each fix and confirming the corresponding tests fail.
- Every consumer of these paths audited and updated: `manifests.py`, `indexing.py`,
  `dependencies.py`, `architecture.py`, `task_context.py`, `workflow_bootstrap.py`,
  `onboarding.py`, `planning.py`, `opencode_adapter.py`. `checkpoints.py` and the
  `.esc-ai/runs/`/`.esc-ai/workflows/active/` locations in `opencode_adapter.py` were
  already correct and needed no change.
- `INSTRUCTIONS.md`'s generated cross-reference to `.esc-ai/workflows/README.md`
  changed from a repo-root-relative link to a same-directory-relative one
  (`workflows/README.md`), since both now live under `.esc-ai/` as siblings.
  `bootstrap_workflow_inheritance`'s "already exists" detection checks the new
  `.esc-ai/INSTRUCTIONS.md` location.
- `import_project_profile`'s legacy `context/project-profile.yaml` read is
  unaffected -- it is an externally-authored file predating and unrelated to this
  convention, and stays exactly where it is.
- All 8 relevant `guides/` docs and this repo's own `README.md`/`INSTRUCTIONS.md`
  updated for the new layout; `examples/contracts/*` fixtures updated for
  consistency.

## Verification

- 127 existing tests pass; regression fixtures for both bug fixes updated/extended
  (see above).
- Manual end-to-end smoke test against a fresh temp Gradle repository (`analyze` ->
  `answer`/`apply`): confirmed on disk that `.esc-ai/esc-execution.yaml`,
  `.esc-ai/components/<id>/esc-component.yaml` (and sibling index/profile files)
  exist, the component's real source directory has no escape-ai files in it at all,
  and `esc-exec architecture check` / `esc-exec dependency generate` both work
  correctly against the real component source tree.
