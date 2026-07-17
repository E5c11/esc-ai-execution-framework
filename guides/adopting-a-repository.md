# Adopting a Repository

This guide describes the Capability 1 adoption flow for a Gradle multi-project
repository. Other build-system adapters will follow the same manifest contracts but
may use different structural discovery.

## 1. Register machine-local routes

The route registry maps stable logical IDs to checkout locations without committing
developer-specific absolute paths:

```bash
esc-exec route add framework esc-ai-execution-framework /path/to/esc-ai-execution-framework
esc-exec route add framework esc-ai-framework /path/to/esc-ai-framework
esc-exec route add repository ampm-backend /path/to/ampm-backend
esc-exec route validate
```

Linux defaults to `~/.config/esc-ai/repositories.yaml`. macOS uses
`~/Library/Application Support/esc-ai/repositories.yaml`; Windows uses the `APPDATA`
directory. `ESC_AI_REGISTRY` or the CLI's `--registry` option may override the location
for CI, tests, or isolated environments.

Routes are local configuration, contain no secrets, and must not be committed into a
consuming repository.

## 2. Generate structural manifests

From the framework checkout:

```bash
esc-exec manifest generate /path/to/repository
```

For Gradle repositories the generator reads `settings.gradle.kts` or
`settings.gradle`, writes the root `esc-execution.yaml`, and writes
`esc-component.yaml` beside each declared Gradle component. It detects existing source,
test, resource, migration, documentation, and build paths.

Regeneration updates fields the adapter can derive and preserves human-authored fields
such as `component.purpose` and `component.ownership`.

## 3. Complete semantic fields

A freshly generated component manifest is intentionally `INCOMPLETE` until a human or
an agent with sufficient project evidence supplies its purpose:

```yaml
component:
  id: content
  type: gradle-module
  path: content
  purpose: Owns curriculum, lesson, and question content retrieval.
  ownership:
    domains: [content, curriculum]
    concerns: [http-api, persistence, publishing]
```

Generators must not invent purpose, ownership, policy, or ambiguous relationships.

## 4. Validate

```bash
esc-exec manifest validate /path/to/repository
```

Validation states and process exit codes are:

| State | Exit | Meaning | Required response |
|---|---:|---|---|
| `VALID` | 0 | Structurally and semantically usable | Continue |
| `INVALID` | 1 | Malformed or contradictory | Correct before use |
| `INCOMPLETE` | 2 | Missing manifest or semantic input | Generate, then request only missing input |
| `STALE` | 3 | Repository structure no longer matches | Regenerate and review the diff |

An invalid result takes precedence when multiple manifests have different states.

## Discovery Convention

Normal discovery is deterministic:

1. Resolve the repository ID through the machine-local registry.
2. Load `<repository>/esc-execution.yaml`.
3. Load only the component manifests explicitly declared by the repository manifest.
4. Match task routing against those declared components.

Do not recursively search a machine for repositories or a repository for undeclared
component manifests. The generator may inspect the build system to detect missing or
new components and report the repository manifest as `STALE`.

## Committed and transient data

Commit repository manifests, component manifests, generated routing indexes, task
specifications, and checkpoints needed to resume work. Do not commit route registries,
raw run logs, caches, model transcripts, or scratch files.
