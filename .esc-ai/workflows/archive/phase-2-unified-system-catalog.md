# Phase 2 — Unified system.yaml Catalog
**Status:** Complete
**Roadmap capability:** 13
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`
(Phase 2) for full rationale; this tracks only this repo's share — Phase 2 is entirely
within this repo.

## Objective

Replace the route-only `repositories.yaml` concept with a versioned machine-local
system catalog that also carries orchestrator/UI defaults and a credential-provider
pointer, with an explicit (never automatic) migration path and actionable repair
messages.

## Deliverables

- [x] `default_registry_path()` now resolves `system.yaml` (was `repositories.yaml`)
      in the same per-platform directory logic; `ESC_AI_REGISTRY` override unchanged.
- [x] `migrate_legacy_registry(new_path)`: explicit, non-interactive migration from a
      sibling legacy `repositories.yaml`. No-op if `system.yaml` already exists or if
      no legacy file is found. Wired into a new `esc-exec system migrate` command —
      never triggered as a side effect of any other command.
- [x] Schema gained three optional top-level sections: `orchestrator.endpoint`
      (non-secret HTTP endpoint preference), `ui` (deliberately open — no concrete
      fields specified by the plan yet), and `credentials.provider` (a pointer to
      which credential provider is configured, e.g. "env" — never an actual secret;
      real secrets stay in the environment or the named provider).
- [x] `validate_registry`'s stale/missing-directory and renamed-framework-ID messages
      now include the exact repair command inline, matching `resolve_route`'s existing
      style, so `route validate` output is directly actionable.
- [x] Guides and `INSTRUCTIONS.md` updated to describe `system.yaml` as the current
      default, with the migration command noted alongside.

## Test coverage

8 new tests in `tests/test_registry.py`: default-path resolution, migration (real
migration, no-op-when-exists, no-op-when-missing), the three new sections validating
correctly, an `additionalProperties: false` rejection inside `credentials`, and the
repair-command text appearing in a stale-route message. 74/74 tests pass overall.

## Noted in passing, out of scope

`guides/adopting-a-repository.md` had a pre-existing stale second framework-route
example (`esc-ai-framework`, the old pre-rename name) unrelated to this deliverable —
left as-is.
