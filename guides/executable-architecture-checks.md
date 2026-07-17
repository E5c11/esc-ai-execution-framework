# Executable Architecture Checks

Architecture decisions that can be evaluated mechanically should be expressed as
component-owned fitness functions with stable rule IDs. Agents run the checks and read
the bounded JSON report before opening source files to diagnose a violation.

## Profile discovery

The canonical profile is `<component>/esc-architecture-profile.yaml`. Its component
manifest declares:

```yaml
paths:
  architecture_profile: esc-architecture-profile.yaml
```

Generate the profile and then author at least one semantic rule:

```bash
esc-exec architecture profile generate ampm-backend content
```

Generation returns `INCOMPLETE` because dependency direction, legacy boundaries, and
required conventions cannot be inferred safely. It creates the correctly located
profile and manifest declaration instead of falling back to implicit global rules.

## Stable rules

Rule IDs use lowercase dot/dash notation and must remain stable when descriptions or
implementation details change. This lets tasks, suppressions, reports, and future UIs
refer to the same architectural decision over time.

Supported first-slice rule types are:

- `forbidden-import` — scan Kotlin and Java imports under a component-relative source
  root using explicit glob patterns.
- `forbidden-path` — fail when a component-relative path glob matches.
- `required-path` — fail when a declared component-relative path is absent.

Example:

```yaml
rules:
- id: content.no-portal-imports
  type: forbidden-import
  description: Content must not depend on portal implementation code.
  source_root: src/main/kotlin
  patterns: [com.esma.ampm.backend.portal.*]
```

Profiles bound violations per rule and evidence characters. Reports include explicit
omission counts, so bounded output can never be mistaken for the complete result.

## Execute and validate

```bash
esc-exec architecture check ampm-backend content \
  .execution/architecture/content.json
esc-exec contract validate architecture-report \
  .execution/architecture/content.json
```

Exit codes follow the framework convention: `0` passes, `1` means one or more rules
failed, and `2` means the profile is missing or incomplete. Rules and filesystem
matches are sorted before evaluation, producing deterministic result ordering.

Add the command to the component gate in `esc-verification-profile.yaml` so
architecture enforcement participates in progressive verification.

## Current boundary

The first slice intentionally avoids a general expression language. New rule types
should be implemented and tested as deterministic adapters. Dependency graph rules
will be added with Capability 10, where generated component relationships can provide
reliable evidence rather than source-import approximations alone.
