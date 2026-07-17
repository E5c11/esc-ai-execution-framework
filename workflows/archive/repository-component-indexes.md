# Repository and Component Indexes
**Status:** Complete
**Roadmap capability:** 2

## Outcome

Generate one canonical JSON routing index for a repository and one detailed JSON index
for each declared component, allowing agents to select search scope before reading
implementation files.

## Decisions

- JSON is the only stored index representation.
- Human-readable views are CLI/UI output generated on demand and are not committed.
- Root indexes stay compact; detailed structure lives in component indexes.
- Indexes contain repository-relative paths and no source contents.
- Input digests contain no timestamps and unchanged inputs produce byte-identical JSON.

## Deliverables

- [x] Repository-index and component-index schemas
- [x] Deterministic JSON generation
- [x] Kotlin package-area and role discovery
- [x] Compact root routing catalog
- [x] Missing/stale/invalid validation
- [x] Deterministic query matching with match reasons
- [x] Unit and CLI regression tests
- [x] `ampm-backend` aliases and generated pilot indexes
- [x] Representative routing-task verification
- [x] Final idempotence and validation pass

## Completion Gate

1. All framework unit tests pass.
2. Repeated generation is byte-identical when inputs do not change.
3. All generated `ampm-backend` indexes validate as `VALID`.
4. Each representative AMPM task routes the intended component first.
5. No Markdown or other duplicate index representation is committed.

## Completion Evidence

- `python -m unittest discover -v`: 17 tests passed.
- `ampm-backend` generated one repository index and nine component indexes; every
  index validates as `VALID`.
- Repeated generation produced byte-identical index files.
- The root index is 5,425 bytes for nine components after component-only search roots
  and entry-point terms were removed from the first-read catalog.
- Seven representative tasks routed the intended component first: password reset,
  lesson publishing, analytics snapshots, institution categories, recommendations,
  UUID serialization, and legacy Firebase migration.
- Only canonical JSON indexes are stored; no Markdown representation was created.
