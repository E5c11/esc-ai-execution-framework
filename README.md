# ESC AI Execution Framework

A provider-agnostic framework for efficient AI-assisted software execution through
component-manifest-driven context routing, repository indexing, verification,
reporting, and durable task checkpoints.

## What This Framework Does

This framework defines how an AI agent should:

- discover and load only task-relevant repository context;
- route work through repository and component manifests;
- execute project-defined development workflows;
- verify changes progressively and summarize results compactly;
- preserve decisions and task state through durable checkpoints; and
- measure and improve execution efficiency.

Consuming repositories keep their project-specific facts—component ownership,
commands, quality gates, active work, and local exceptions—in committed manifests and
their own workflow documents.

## Relationship to ESC AI Framework

The two frameworks are complementary:

- **ESC AI Framework** defines how software should be designed and implemented.
- **ESC AI Execution Framework** defines how agents should discover context, execute
  work, verify outcomes, and communicate state efficiently.

This repository does not own application architecture or implementation-pattern rules.

## Initial Runtime

[OpenCode](https://opencode.ai/) is the initial reference agent runtime because it
provides a lightweight headless server, API, sessions, agents, permissions, and plugin
model. This is an implementation choice for getting the first vertical slices running,
not a permanent dependency of the framework contract. The framework must remain usable
by other agent runtimes and orchestrators.

## Getting Started

See [`INSTRUCTIONS.md`](./INSTRUCTIONS.md) for the canonical usage model and
[`.esc-ai/workflows/README.md`](./.esc-ai/workflows/README.md) for framework
development work.

Implemented in Python. See the active framework
[`roadmap`](./.esc-ai/workflows/active/roadmap.md) for the agreed sequence.

## CLI

`esc-exec` has grown well past its original bootstrap slice. Command groups, by
area:

- `route` — machine-local repository/framework routes (`add`, `resolve`, `list`,
  `validate`, `ecosystem add`)
- `system` — the machine-local system catalog itself (`migrate`)
- `repository` — onboarding analysis and answer application (`analyze`, `answer`)
- `manifest` — repository/component manifests (`generate`, `validate`)
- `index` — JSON routing indexes (`generate`, `validate`, `match`)
- `contract` — provider-neutral execution contracts (`validate`, `validate-set`)
- `report` — bounded summaries of retained reports (`summarize`)
- `context` — bounded task-specific routing context (`build`)
- `verification` — progressive verification plans (`profile generate`, `plan`,
  `execute`)
- `architecture` — component architecture fitness functions (`profile generate`,
  `check`)
- `checkpoint` — durable task handoffs (`create`, `update`, `inspect`)
- `dependency` — component dependencies and consumer/impact analysis
  (`generate`, `validate`, `impact`)
- `measurement` — execution-efficiency comparisons (`compare`)
- `opencode` — the OpenCode reference adapter (`execute`, `fork`)

The original bootstrap slice is still the simplest way to onboard and index a
repository:

```bash
python -m esc_exec route add repository my-project /path/to/my-project
python -m esc_exec manifest generate /path/to/my-project
python -m esc_exec manifest validate /path/to/my-project
python -m esc_exec index generate my-project
python -m esc_exec index validate my-project
python -m esc_exec index match my-project "lesson publishing"
```

For editable installation during framework development:

```bash
python -m pip install -e .
esc-exec --help
```

See [`guides/adopting-a-repository.md`](./guides/adopting-a-repository.md) for the
complete adoption flow.

Provider-neutral task, run, event, artifact, checkpoint, workspace, adapter, and policy
contracts are documented in [`guides/execution-contracts.md`](./guides/execution-contracts.md).
Bounded test-result summaries and component-owned report profiles are documented in
[`guides/compact-verification-reports.md`](./guides/compact-verification-reports.md).
Bounded task routing and progressive verification gates are documented in
[`guides/task-context-and-progressive-verification.md`](./guides/task-context-and-progressive-verification.md).
Component-owned architecture fitness functions are documented in
[`guides/executable-architecture-checks.md`](./guides/executable-architecture-checks.md).
Committed task handoffs and transient failed-run candidates are documented in
[`guides/durable-checkpoints.md`](./guides/durable-checkpoints.md).
Generated consumer graphs and impact-gate selection are documented in
[`guides/dependency-impact-analysis.md`](./guides/dependency-impact-analysis.md).
Portable run metrics and baseline/candidate comparisons are documented in
[`guides/execution-efficiency-measurement.md`](./guides/execution-efficiency-measurement.md).
The initial runtime implementation is documented in
[`guides/opencode-adapter.md`](./guides/opencode-adapter.md).

Generated indexes are canonical, pretty-printed JSON files named `esc-index.json`.
There is deliberately no committed Markdown representation; human views are rendered
on demand by the CLI or future user interfaces.
