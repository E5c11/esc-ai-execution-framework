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
`paths.verification_profile` in `esc-component.yaml`. Generate a Gradle starting point:

```bash
esc-exec verification profile generate ampm-backend content
esc-exec index generate ampm-backend
```

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

The orchestrator materializes the plan before starting OpenCode, so missing profiles
block early with a generation command. Generated run artifacts are available through
`GET /runs/{id}/context` and `GET /runs/{id}/verification-plan`.

## Current boundary

This capability plans gates but does not execute their commands. A future verification
runtime will resolve focused inputs, run ready checks under policy, and attach compact
verification summaries. Capability 10 will generate dependency-driven impact checks.
