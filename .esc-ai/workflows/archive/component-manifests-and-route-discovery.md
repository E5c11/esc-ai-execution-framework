# Component Manifests and Route Discovery
**Status:** Complete
**Roadmap capability:** 1

## Outcome

Provide deterministic repository resolution and component discovery without committed
absolute paths or broad filesystem searches.

## Deliverables

- [x] Repository-manifest JSON Schema
- [x] Component-manifest JSON Schema
- [x] Machine-local route-registry JSON Schema
- [x] Cross-platform route-path resolution with overrides
- [x] Route add, list, resolve, and validate commands
- [x] Gradle multi-project structural discovery
- [x] Safe manifest regeneration preserving human semantic fields
- [x] `VALID`, `INVALID`, `INCOMPLETE`, and `STALE` states
- [x] Unit tests for generation, merging, routing, and staleness
- [x] Repository adoption guide
- [x] `ampm-backend` pilot manifests
- [x] Machine-local routes for the pilot repositories/frameworks
- [x] Final test and pilot validation pass

## Completion Gate

1. `python -m unittest discover -v` passes.
2. Route-registry validation succeeds for the pilot routes.
3. `ampm-backend` generates without overwriting semantic fields on a second run.
4. Every declared `ampm-backend` component validates as `VALID`.
5. The active roadmap marks Capability 1 complete and Capability 2 next.

## Completion Evidence

- `python -m unittest discover -v`: 10 tests passed.
- The real machine-local registry validates and resolves `ampm-backend`,
  `esc-ai-framework`, and `esc-ai-execution-framework`.
- `ampm-backend` contains one repository manifest and nine declared component
  manifests; every manifest validates as `VALID`.
- A second generation pass preserved the human-authored component purposes.
- The pilot exposed and fixed a `repository` → `repositorys` pluralization defect;
  CLI-level regression coverage now protects the route registry shape.
