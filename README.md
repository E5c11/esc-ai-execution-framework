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

The framework is at an early bootstrap stage. See [`INSTRUCTIONS.md`](./INSTRUCTIONS.md)
for the canonical usage model and [`workflows/README.md`](./workflows/README.md) for
framework development work.

The implementation language will be selected while specifying the first executable
vertical slice. See the active framework
[`roadmap`](./workflows/active/roadmap.md) for the agreed sequence.

## Bootstrap CLI

Capability 1 uses a small Python CLI for route registration and Gradle component
manifest generation:

```bash
python -m esc_exec route add repository my-project /path/to/my-project
python -m esc_exec manifest generate /path/to/my-project
python -m esc_exec manifest validate /path/to/my-project
```

For editable installation during framework development:

```bash
python -m pip install -e .
esc-exec --help
```

See [`guides/adopting-a-repository.md`](./guides/adopting-a-repository.md) for the
complete adoption flow.
