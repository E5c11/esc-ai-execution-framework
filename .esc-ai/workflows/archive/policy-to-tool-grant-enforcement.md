# Policy-to-Tool-Grant Enforcement
**Status:** Complete
**Roadmap capability:** 18

## Objective

Close a gap flagged since Phase 1 of `esc-ai-orchestrator`'s
`plan/cohesive-system-integration-and-onboarding.md`: `OpenCodeAdapter` sent a
hardcoded `READ_ONLY_TOOLS` tool grant with every run, regardless of what the run's
own `policy.yaml` declared under `permissions`. The policy contract was
schema-validated but never behaviorally enforced — a policy declaring `edit: allow`
had no effect on what the runtime could actually do.

## What changed

- `esc_exec/opencode_adapter.py`: new `tools_for_policy(policy_document)` maps
  `permissions.read/edit/execute/network` (`allow`/`ask`/`deny`) onto the OpenCode
  tool grant (`read`/`list`/`glob`/`grep`, `edit`/`write`/`patch`, `bash`,
  `webfetch`). Deny-by-default; `ask` is treated as denied (no mid-run
  human-escalation mechanism exists yet to actually honor it); `task`/`todowrite`/
  `todoread` aren't covered by any permission category and stay denied
  unconditionally. `permissions.external_paths` and the policy document's
  `limits`/`approvals` fields are explicitly **not** enforced by this function —
  they need path-scoping and run-duration/approval gating, a different mechanism
  entirely. That gap is real and this change does not close it; said plainly in the
  function's docstring and in `guides/opencode-adapter.md` rather than left implicit.
- Fixed a bug caught by the fix's own first test run: `OpenCodeAdapter.execute` was
  loading only `policy_path["policy"]` (the `{id, description}` sub-object) into its
  `policy` variable, not the full document — `permissions` is a sibling top-level
  key, not nested under `policy`. `tools_for_policy` needs the whole document; fixed
  by keeping a separate `policy_document` reference.
- `OpenCodeClient.prompt()` now takes the computed tool grant as an explicit
  parameter instead of reaching for the module-level constant. `READ_ONLY_TOOLS` is
  removed — fully superseded, not dead-code-preserved.
- `OpenCodeAdapter._prompt()`'s constraint line ("Operate read-only...") is now
  derived from the actual grant instead of hardcoded, so the prompt text doesn't
  contradict a policy that actually allows edits.
- The computed tool grant is recorded at `run.bindings.tool_grant` in `run.json` —
  a run's actual permissions are now auditable after the fact, not just inferable by
  re-reading the policy file. `schemas/run.schema.json` documents the new field.

## Verification

122/122 tests pass (11 in `test_opencode_adapter.py`, up from 3 — new coverage:
each permission category's mapping in isolation, `ask` treated as denied, missing
`permissions` denies everything, `task`/`todowrite`/`todoread` never grantable, and
an end-to-end `execute()` run with a real `edit: allow` policy confirming the fake
client receives the correct grant, the prompt text matches it, and `run.json`
records it and still validates). The existing read-only spike test
(`examples/contracts/policy.yaml`) was verified unchanged byte-for-byte in its
resulting tool grant — no regression to the original hardcoded behavior for that
policy, only for the previously-ignored ones.

## Not fixed here (real, still open)

`permissions.external_paths` and the policy document's `limits`/`approvals` fields
remain unenforced. Approving something valued `ask` still simply denies it — a real
human-escalation path for `ask` is future work, not attempted here.
