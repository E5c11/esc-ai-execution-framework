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

- `forbidden-import` — scan imports under component-relative source roots using explicit
  glob patterns. Kotlin and Java (`.kt`, `.java`) match the imported name; TypeScript and
  JavaScript (`.ts .tsx .mts .cts .js .jsx .mjs .cjs`) match the module specifier as written
  and, when it is a local import, the component-relative path it resolves to.
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

### Checking TypeScript and JavaScript

A TypeScript rule reads `import … from`, `import type`, side-effect `import '…'`,
`export … from`, `import('…')` and `require('…')` with a string literal. It resolves
relative imports and the aliases in the component's `tsconfig.json` (`paths`, `baseUrl`,
and relative `extends` chains), so `@/features/a/data/X` is matched as
`src/features/a/data/X`. It does not see a dynamic import whose argument is computed, which
is a limit of reading source rather than running it.

Patterns use glob syntax where `**` crosses directories, `*` stays inside one path segment
and `?` is one character. A package is written as `firebase` plus `firebase/**`. Extra rule
fields for these rules:

| Field | Meaning |
|---|---|
| `source_roots` | A list of directory globs, such as `src/features/*/domain`. Replaces `source_root` |
| `exclude` | File globs the rule does not scan, such as `**/*Page.tsx` |
| `include_tests` | Scan `*.test.*`, `*.spec.*`, `*.contract.*` and `__tests__`. Off by default, because tests build their own graphs |
| `except_own_feature` | An import that resolves inside the directory the root glob matched is not a violation, so a feature may import its own internals |
| `optional` | A root glob that matches no directory passes. For layer folders that exist only once the feature needs them |

A profile may declare `pattern_sets`, named lists a rule includes with `"{name}"`, so a list
of provider SDKs is written once. `node_modules`, `.next`, `out`, `dist`, `build`,
`coverage` and `.turbo` are never scanned.

### A rule that scans nothing fails

A `forbidden-import` rule that scans no source files reports a violation saying so, and the
report's status is `failed`. A contract that passes because it checked nothing (a root that
exists but holds no code of the rule's language, a layout that is not the one the profile
assumes) is worse than one that fails, because nobody reads it again. Each result carries
`files_scanned`, so a count of zero is visible. A root glob that matches no directory at all
is an error, except for an `optional` rule.

### Starting from a template

```bash
esc-exec architecture profile generate my-repo web --template web-app
```

writes a profile with the mechanical dependency rules of the `web-app` architecture
(`ARCH-WEB-APP-DEP-01` to `-07` in `esc-ai-architecture-framework`), with the layer folders
detected under `src/` or the component root, and the provider SDKs found in `package.json`
added to a `provider-sdks` pattern set. It includes a `required-path` rule for the `features`
and `app` folders, so a project whose layout is not the assumed one fails there instead of
passing the rest while checking nothing. A template is a starting point: read the result.

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
