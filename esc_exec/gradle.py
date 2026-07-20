from __future__ import annotations

import re
from pathlib import Path


# Matches an entire `include(...)`/`include ...` statement -- one or more
# comma-separated quoted module paths, with or without the enclosing parens
# (Kotlin DSL always uses parens; Groovy DSL commonly omits them), and tolerant
# of the whole statement being spread across multiple lines (both `\s` and the
# repeated group below match newlines) -- e.g. `include(":app", ":core")`,
# `include ':app', ':core'`, or a long list with one module per line.
INCLUDE_RE = re.compile(r'''\binclude\b\s*\(?\s*((?:["'][^"']+["']\s*,?\s*)+)\)?''')
MODULE_PATH_RE = re.compile(r'''["'](:[^"']+)["']''')
ROOT_NAME_RE = re.compile(r'''rootProject\.name\s*=\s*["']([^"']+)["']''')
# `project(":path").projectDir = file("dir")` -- lets a module's on-disk folder
# name differ from its colon-derived default (e.g. a `:arrow-errors-core`
# Gradle project path namespaced for the build graph, living in an `error-core/`
# directory). Without parsing this, the directory-existence check below looks
# in the wrong place for any remapped module and silently drops it -- found
# against a real repository where every remapped module vanished and only the
# one unremapped module (whose folder happened to match its colon path) survived.
PROJECT_DIR_RE = re.compile(
    r'''project\s*\(\s*["'](:[^"']+)["']\s*\)\s*\.\s*projectDir\s*=\s*(?:new\s+)?(?:file|File)\s*\(\s*["']([^"']+)["']\s*\)'''
)


def _included_module_paths(text: str) -> list[str]:
    paths: list[str] = []
    for statement in INCLUDE_RE.findall(text):
        paths.extend(MODULE_PATH_RE.findall(statement))
    return paths


def _project_dir_overrides(text: str) -> dict[str, str]:
    return dict(PROJECT_DIR_RE.findall(text))


def _resolve_components(
    root: Path, text: str,
) -> tuple[list[tuple[str, Path]], list[str], dict[str, str]]:
    """Returns (resolved components, gradle paths include(...) declared that
    couldn't be resolved to a real directory even after projectDir remapping --
    the latter is a signal for a Tier 2 fallback, not something this function
    itself invents an answer for -- and a component_id -> real gradle project path
    map, since a projectDir-remapped component's directory no longer matches its
    colon path, and generate_gradle_manifests needs the real one for the
    dependency graph's `project(":x")` matching, not something reconstructed from
    the (now-divergent) directory)."""
    overrides = _project_dir_overrides(text)
    resolved: list[tuple[str, Path]] = []
    unresolved: list[str] = []
    project_paths: dict[str, str] = {}
    for gradle_path in _included_module_paths(text):
        override = overrides.get(gradle_path)
        relative = Path(override) if override else Path(*gradle_path.lstrip(":").split(":"))
        if (root / relative).is_dir():
            component_id = gradle_path.lstrip(":").replace(":", "-")
            resolved.append((component_id, relative))
            project_paths[component_id] = gradle_path
        else:
            unresolved.append(gradle_path)
    return resolved, unresolved, project_paths


def detect_gradle_repository(root: Path) -> tuple[str, list[tuple[str, Path]]]:
    settings = next(
        (candidate for candidate in (root / "settings.gradle.kts", root / "settings.gradle") if candidate.exists()),
        None,
    )
    if settings is None:
        raise ValueError(f"No settings.gradle.kts or settings.gradle found under {root}")
    text = settings.read_text(encoding="utf-8")
    name_match = ROOT_NAME_RE.search(text)
    repository_id = name_match.group(1) if name_match else root.name
    components, _unresolved, _project_paths = _resolve_components(root, text)
    if not components and (
        (root / "build.gradle.kts").exists() or (root / "build.gradle").exists()
    ):
        # A single-module Gradle repository (no include(...) subprojects, e.g. a
        # small published library) still has exactly one real, buildable component:
        # the root project itself. Without this, such a repository detects zero
        # components and can never be onboarded for dependency/impact tracking.
        components.append((repository_id, Path(".")))
    return repository_id, components


def unresolved_gradle_includes(root: Path) -> list[str]:
    """
    Gradle project paths this repository's settings file declares via
    include(...) that Tier 1 static parsing could not resolve to a real
    directory -- distinct from a repository that legitimately has zero declared
    modules (that case returns [] here too, but detect_gradle_repository still
    falls back to the single-module case for it). A non-empty result is the
    signal a Tier 2 AI fallback should key off of, e.g. modules included
    programmatically (looping over a directory listing) rather than as plain
    include(":name") calls with a fixed, statically-parseable name.
    """
    settings = next(
        (candidate for candidate in (root / "settings.gradle.kts", root / "settings.gradle") if candidate.exists()),
        None,
    )
    if settings is None:
        return []
    text = settings.read_text(encoding="utf-8")
    _resolved, unresolved, _project_paths = _resolve_components(root, text)
    return unresolved


def gradle_project_paths(root: Path) -> dict[str, str]:
    """
    component_id -> real Gradle project path (`:core:api`), for every
    include(...)-declared, directory-resolved component -- the actual project
    path a `project(":core:api")` dependency declaration elsewhere in the
    repository would reference, which is NOT always reconstructable from a
    component's directory once projectDir remapping is in play (see
    _resolve_components). Empty dict for a single-module repository (no
    include(...) at all -- there's no meaningfully distinct "project path" for
    the root project itself, callers fall back to Gradle's own root-project
    default of an empty path segment).
    """
    settings = next(
        (candidate for candidate in (root / "settings.gradle.kts", root / "settings.gradle") if candidate.exists()),
        None,
    )
    if settings is None:
        return {}
    text = settings.read_text(encoding="utf-8")
    _resolved, _unresolved, project_paths = _resolve_components(root, text)
    return project_paths


def component_structure(root: Path, relative: Path) -> dict[str, str]:
    component = root / relative
    candidates = {
        "source": component / "src/main/kotlin",
        "tests": component / "src/test/kotlin",
        "resources": component / "src/main/resources",
        "test_resources": component / "src/test/resources",
        "migrations": component / "src/main/resources/db/migration",
        "documentation": component / "README.md",
        "build": next(
            (path for path in (component / "build.gradle.kts", component / "build.gradle") if path.exists()),
            component / "build.gradle.kts",
        ),
    }
    paths = {
        key: str(path.relative_to(component))
        for key, path in candidates.items()
        if path.exists()
    }
    if "source" not in paths and "tests" not in paths:
        # Kotlin Multiplatform modules don't use the plain JVM src/main|test/kotlin
        # layout at all -- every source set (commonMain, androidMain, iosMain, ...)
        # lives directly under src/, conventionally suffixed Main/Test. Without this,
        # a KMP component's manifest ends up with no source/tests paths at all, and
        # task-context routing gives an AI agent no search_roots to look in for it --
        # found while onboarding two real KMP repositories end to end.
        source_root = component / "src"
        if source_root.is_dir():
            for entry in sorted(source_root.iterdir()):
                if not entry.is_dir() or not (entry / "kotlin").is_dir():
                    continue
                if entry.name.endswith("Test"):
                    paths[f"tests_{entry.name}"] = str((entry / "kotlin").relative_to(component))
                elif entry.name.endswith("Main"):
                    paths[f"source_{entry.name}"] = str((entry / "kotlin").relative_to(component))
    return paths

