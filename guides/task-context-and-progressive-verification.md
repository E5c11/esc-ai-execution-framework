# Task Context and Progressive Verification

Capability 7 creates two small generated inputs for an agent run:

- `task-context.json` identifies only the declared components, their indexes, bounded
  search roots, task paths, references, and completion conditions.
- `verification-plan.json` orders component-owned checks through focused, component,
  impact, and final gates.

Neither file embeds source code or command output. The indexes and complete reports
remain the authoritative sources when deeper inspection is required.

## Build task context

```bash
esc-exec context build ampm-backend task.yaml .execution/task-context.json
esc-exec contract validate task-context .execution/task-context.json
```

Default bounds are 10 components, 30 explicit paths, and 30 references. If a task
exceeds them, split the task or raise a limit explicitly. Generation never silently
drops scope. Every declared component must exist in the current repository index.

OpenCode runs generate this context before prompting the agent. The prompt directs the
agent to the root index, selected component indexes, and their bounded search roots.

## Component-owned verification profiles

The canonical component profile is `esc-verification-profile.yaml`, declared by
`paths.verification_profile` in `esc-component.yaml` -- both colocated in that
component's manifest bundle under `.esc-ai/components/<component-id>/`. Generate a
Gradle starting point:

```bash
esc-exec verification profile generate ampm-backend content
esc-exec index generate ampm-backend
```

npm components (`build.system: npm`) get a profile built from the component's own
`package.json` scripts: `esc-exec verification profile generate <repository> <component>`
picks the generator from the manifest. The focused gate runs the `test` script with a
`{test_filter}` argument; the component gate runs `typecheck` (or `tsc --noEmit` when
TypeScript is installed), `lint` and `test`; the final gate runs the `build` script, preceded
by the repository's `test` script for a workspace member. The package manager is read from
the lockfile (`pnpm`, `yarn`, `bun`, else `npm`), a workspace member runs in its own
directory, and a bare `vitest` script is run as `vitest run` so it ends. A component with no
`test` script gets no profile (onboarding reports it under `skipped_verification_profiles`),
because a test gate that runs nothing would report "verified". A missing `lint`, `typecheck`
or `build` is written to `profile.notes` so the gap is visible; Next.js 16 removed
`next lint` and `next build` no longer lints, so a project without a `lint` script has
unlinted code that every gate passes.

Generation derives Gradle task names from the component manifest. It does not invent a
focused test selector or dependency impact. The focused gate therefore contains a
`{test_filter}` input and is marked `input-required`; impact remains `not-applicable`
until the profile provides checks or dependency analysis supplies them.

Profiles declare commands as argument arrays rather than shell strings. This preserves
argument boundaries and allows a later executor to apply policy without reparsing a
shell command.

## Build the progressive plan

```bash
esc-exec verification plan ampm-backend task.yaml .execution/verification-plan.json
esc-exec contract validate verification-plan .execution/verification-plan.json
```

The fixed order is:

1. `focused` — the smallest task-related tests; required inputs must be resolved first.
2. `component` — the complete checks owned by every selected component.
3. `impact` — checks for known consumers; explicitly not applicable until declared.
4. `final` — repository quality gates.

Execution stops at the first failed gate. Checks from multiple selected components are
merged deterministically and identical command arrays are deduplicated.

The orchestrator materializes the plan before dispatching any adapter (Claude Code,
OpenCode, or Codex — the same `_AdapterRuntime` wraps all three), so missing profiles
block early with a generation command. Generated run artifacts are available through
`GET /runs/{id}/context` and `GET /runs/{id}/verification-plan`.

## Execution

Gates are executed for real, independent of whichever adapter did the agent work
(`execute_verification_plan`): real subprocess exit codes and durations, JUnit reports
summarized and attached per check, execution stops at the first failed gate. The
Scheduler reads this result, not the agent's own claim of success, to decide whether a
run succeeded. Dependency-driven impact checks are generated too, not future work: the
`impact` gate merges the `component` gate's checks from every transitive consumer of
the task's declared components, from real dependency-graph analysis.

## Current boundary

The one gap that remains: the `focused` gate's `{test_filter}` input is never resolved
automatically. A Gradle-generated profile always marks it `requires: [test_filter]`, so
its status is `input-required` and it is skipped at execution time — only `component`,
`impact`, and `final` actually run today. Resolving a real focused test selector (and
running it under policy) is the remaining future work here.
