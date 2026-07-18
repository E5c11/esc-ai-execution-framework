# Dependency Graph and Impact Analysis

The generated component graph answers the verification question that repository search
cannot: which declared components consume the component being changed?

## Canonical graph

Generate and commit the root `.esc-ai/esc-dependencies.json`:

```bash
esc-exec dependency generate ampm-backend
esc-exec dependency validate ampm-backend
```

The graph contains declared component nodes and directed edges from consumer to
dependency. For Gradle, edges are derived from calls such as:

```kotlin
implementation(project(":content"))
```

Node and edge ordering is deterministic. The input digest covers the repository and
component manifests plus component build files, so a build dependency change makes the
graph stale and blocks impact planning until regeneration.

Only components declared in `.esc-ai/esc-execution.yaml` appear as nodes. The root
application is not treated as an undeclared component; repository-level verification
remains in the final gate. Each component's `build.gradle.kts` is resolved relative to
its real source path (`component.path` under the repository root), never relative to
where that component's manifest bundle happens to be stored.

## Analyze consumers

```bash
esc-exec dependency impact ampm-backend content \
  --output .execution/impact/content.json
esc-exec contract validate impact-analysis .execution/impact/content.json
```

The result distinguishes direct consumers from the full transitive consumer closure.
`affected_components` contains the changed sources plus all transitive consumers.

## Progressive verification integration

When building `verification-plan.json`, the framework:

1. validates the current dependency graph;
2. computes transitive consumers of the task's declared source components;
3. loads each consumer's declared `esc-verification-profile.yaml`;
4. adds the consumer's component checks to the impact gate; and
5. deduplicates identical command arrays deterministically.

A missing consumer profile stops plan generation with the exact profile-generation
command. It never silently omits an impacted component.

For a `content` task in `ampm-backend`, the generated graph selects
`:recommendations:test` for the impact gate because `recommendations` directly depends
on `content`. The final `./gradlew test` gate still covers the assembled root service.

## Current parser boundary

The first Gradle adapter recognizes string-based `project(":path")` dependency calls
inside named configurations such as `implementation`, `api`, and
`testImplementation`. Gradle type-safe project accessors and dynamically constructed
dependencies require future parser adapters. Unsupported syntax is not guessed; the
generated graph should be reviewed like other derived manifests.
