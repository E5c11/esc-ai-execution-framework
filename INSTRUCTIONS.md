# ESC AI Execution Framework — Instructions

## What This Is

A provider-agnostic execution framework for AI-assisted software development. It
defines how agents select context, navigate repositories, run project workflows,
verify outcomes, summarize tool output, and preserve state efficiently.

The AI model is an interchangeable execution engine. This framework supplies the
execution process; the consuming project supplies project-specific facts.

**This is the canonical source for how to use the framework.** Provider-specific
entry files are thin pointers to this document and must not duplicate its content.

## Scope

This framework will cover:

- repository and component manifests;
- machine-local repository/framework route registration;
- repository and component context routing;
- task specifications;
- progressive verification;
- compact test, coverage, compiler, and tool reporting;
- dependency-aware change impact;
- durable task checkpoints; and
- execution-efficiency measurement.

This framework does not define application architecture, coding patterns, business
rules, or project-specific implementation requirements. Those belong to the ESC AI
Framework and the consuming project's own documentation or framework extensions.

## How Consuming Projects Use It

Each consuming repository provides a committed repository manifest at:

```text
esc-execution.yaml
```

Each participating module or package owns a committed component manifest colocated at
its root:

```text
<component>/esc-component.yaml
```

The repository manifest explicitly declares its component manifests. Normal discovery
must not recursively scan for undeclared manifests.

Manifests contain only project-specific execution facts, such as:

- project identity and technology profile;
- component identity, ownership, and source/test roots;
- repository ownership and routing metadata;
- build, test, coverage, and static-analysis commands;
- structured report locations;
- final quality gates; and
- links to active project workflows and engineering rules.

The exact manifest schemas are not yet finalized. Until they are, do not create
competing local conventions in consuming repositories.

## Repository and Framework Resolution

Absolute checkout paths are machine-specific and must not be committed to consuming
repositories. A machine-local route registry resolves stable repository and framework
IDs to absolute paths. Its provisional Linux location is:

```text
~/.config/esc-ai/repositories.yaml
```

The orchestrator must flag missing or stale routes and request registration rather than
silently searching arbitrary parent directories. Repository and framework manifests
refer to logical IDs, never developer-specific absolute paths.

If a repository or component manifest is absent or incomplete, generate all reliably
derivable structural facts first and request human input only for semantic facts such
as purpose, ownership, policy, or ambiguous relationships. Bounded manual discovery is
a last resort when generation cannot run, not the normal path.

## Provisional Execution Flow

Until more detailed specifications replace it, agents should use this conceptual
flow:

1. Resolve repository and framework IDs through the machine-local route registry.
2. Read the consuming project's repository manifest.
3. Route the task through the repository index and matching component manifests.
4. Load only the relevant project files, workflow requirements, and engineering rules.
5. Execute the smallest useful verification during implementation.
6. Expand verification according to dependency impact and project quality gates.
7. Return compact results with paths to complete reports when deeper inspection is
   needed.
8. Preserve durable decisions and unfinished task state in a committed checkpoint
   associated with the active task.

Manifests and indexes narrow initial discovery. They do not prohibit evidence-driven
scope expansion and are not substitutes for reading source code, tests, or reports
necessary to complete a task correctly.

## Terminology

- **Repository manifest** — committed description of a repository and its declared
  components.
- **Component manifest** — committed description of a module or package, its ownership,
  paths, relationships, and execution facts.
- **Profile** — a selectable execution configuration such as local, CI, or release; it
  is not the identity document for a repository or component.
- **Task specification** — structured definition of requested work and its completion
  conditions.
- **Run** — one attempt to execute a task.
- **Event** — structured activity emitted during a run.
- **Artifact** — file or report produced by a run.
- **Checkpoint** — committed resumable state for unfinished work.
- **Workspace** — repository environment in which a run operates.
- **Adapter** — integration with an agent runtime, build system, test framework, or
  reporting tool.
- **Policy** — permission, safety, or execution constraint applied to a run.

## Framework Development

This repository is being built incrementally. Work on only one execution capability at
a time, validate it against a real consuming repository, and generalize it only after
the vertical slice proves useful.

Framework development and extension work lives under [`workflows/`](./workflows/).
Do not place consuming-project feature workflows there.

## Current Bootstrap Decisions

- The framework is provider-agnostic.
- `INSTRUCTIONS.md` is the single canonical instruction source.
- Consuming repositories remain authoritative for their project-specific facts.
- Repository and component manifests are committed; machine-local checkout routes are
  not.
- Structural facts are generated where reliable; semantic facts remain human-authored.
- Missing or incomplete manifests trigger generation before manual fallback.
- Schema documents carry an explicit integer `schema_version` from their first version.
- OpenCode is the initial reference runtime, not part of the portable framework
  contract.
- Generic tooling and schemas belong here rather than being copied into consumers.
- A future central orchestrator will live separately from this specification/framework
  repository once the provider-neutral contracts have been exercised.
- The implementation language remains undecided until the first executable vertical
  slice is specified.
- Initial documents are intentionally minimal and will be refined as capabilities are
  designed and exercised.
