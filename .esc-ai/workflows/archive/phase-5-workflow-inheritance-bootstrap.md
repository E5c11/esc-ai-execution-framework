# Phase 5: Workflow Inheritance Bootstrap
**Status:** Complete
**Roadmap capability:** 16
**Plan:** see `esc-ai-orchestrator/plan/cohesive-system-integration-and-onboarding.md`,
Phase 5, for full rationale; this tracks only this repo's share (Part A of a
two-repo round -- Part B is `esc-ai-orchestrator` confirming the answers endpoint
passes the bootstrap outcome through).

## Delivered

- `schemas/workflow-policy.schema.yaml`: the frontmatter block governing
  `.esc-ai/workflows/README.md` -- `schema_version`, and an optional `policy` object
  (`extension.id`/`extension.precedence`, `final_gates`, `commit_conventions`).
  Validated manually (`validate_workflow_policy`), matching this repo's established
  convention of hand-checked fields rather than a jsonschema dependency.
- `esc_exec/workflow_bootstrap.py`:
  - Renders a thin `INSTRUCTIONS.md` pointer (references both frameworks by stable
    ID, does not duplicate either), a `.esc-ai/workflows/README.md` skeleton
    (structured frontmatter + prose covering extension/naming/roadmap-location/
    exceptions, explicitly labeled as a starting skeleton, not a finished policy),
    and minimal `active/README.md`/`archive/README.md` pointers.
  - `bootstrap_workflow_inheritance(root, repository_manifest)` is idempotent:
    creates each of the four files only if missing; a file that already exists is
    left completely untouched and reported as `existing`, never regenerated. This is
    the direct implementation of the "never overwrite a mature workflow package"
    non-goal and the "Who this is for" scope boundary -- existing content is a
    signal for a human, not something to silently replace.
  - `check_thin_pointer` is an advisory-only self-check run against files this
    function itself just generated (never against pre-existing content, which isn't
    reliably checkable this way) -- flags if a generated pointer grew unusually long
    or appears to contain an embedded framework document's own frontmatter.
- Wired into `apply_onboarding_answers` as its final step; the result now includes a
  `workflow_inheritance` key (`created`/`existing`/`advisory_warnings`).

## Decision resolved

Plan doc's "Decisions required before implementation" item 4 (structured vs.
free-form instruction fields): structured YAML frontmatter for the genuinely
enumerable fields (extension reference, precedence, final-gate commands), free-form
Markdown prose for the rest -- matching the frontmatter-plus-prose convention the
architecture framework's own documents already use.

## Verification

- 105 tests pass (7 new: 6 in `test_workflow_bootstrap.py`, 1 in `test_onboarding.py`).
- Manually verified end-to-end via the real CLI (`python3 -m esc_exec repository
  analyze` -> `repository answer` against a fresh temp Gradle repository):
  `INSTRUCTIONS.md` and all three `.esc-ai/workflows/` files were generated with
  correct cross-references; re-running is a no-op.
