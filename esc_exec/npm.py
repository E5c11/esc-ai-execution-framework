from __future__ import annotations

import json
from pathlib import Path

from esc_exec.yaml_io import load_yaml


def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


# Maps a detected npm package name to the (field, value) pair `suggest_profile_ids`
# expects -- mirrors the architecture framework's own profile-doc-map.json vocabulary
# as of 2026-07-20, same as GRADLE_COORDINATE_FRAMEWORKS (esc_exec/dependencies.py). A
# package not listed here is simply not detected, never miscategorized -- this table
# will drift out of sync as that framework's vocabulary grows and needs updating
# alongside it, not silently.
NPM_PACKAGE_FRAMEWORKS: tuple[tuple[str, str, str], ...] = (
    ("next", "ui", "next"),
    ("react-hook-form", "forms", "react-hook-form"),
)


def detect_npm_frameworks_and_targets(package_json_path: Path) -> tuple[dict[str, str], list[str]]:
    """
    Best-effort static detection of an npm component's frameworks-in-use directly
    from its package.json -- Tier 1 of
    plan/onboarding-answer-detection-and-suggestion.md, the npm counterpart to
    detect_gradle_frameworks_and_targets (esc_exec/dependencies.py), added by
    plan/npm-architecture-profile-detection.md. Returns ({}, []) if the file
    doesn't exist, can't be read, or isn't valid JSON; never raises, since this is
    an optional enrichment, not a required step.

    Reads both "dependencies" and "devDependencies" and matches each package name
    against NPM_PACKAGE_FRAMEWORKS by exact string equality -- unlike Gradle's
    "group:artifact" coordinates, npm package names have no meaningful prefix
    structure to match against, so this is a plain lookup, not a startswith scan.
    Only packages in NPM_PACKAGE_FRAMEWORKS are ever detected -- an unrecognized
    dependency is silently not reported, not guessed at. As with the Gradle
    version, the first match per field wins if more than one installed package
    happened to map to the same field.

    targets is always [] -- there is no npm-side equivalent yet of Gradle's KMP-iOS
    target detection (see plan/npm-architecture-profile-detection.md, design
    section 1); returned only to keep this function's contract shaped exactly like
    detect_gradle_frameworks_and_targets's for FRAMEWORK_DETECTORS dispatch.
    """
    package_json = _read_json(package_json_path)
    if not package_json:
        return {}, []

    names: set[str] = set()
    for key in ("dependencies", "devDependencies"):
        section = package_json.get(key)
        if isinstance(section, dict):
            names |= {name for name in section if isinstance(name, str)}

    frameworks: dict[str, str] = {}
    for package_name, field, value in NPM_PACKAGE_FRAMEWORKS:
        if field in frameworks:
            continue
        if package_name in names:
            frameworks[field] = value
    return frameworks, []


def _package_name(package_json: dict, fallback: str) -> str:
    name = package_json.get("name")
    return name if isinstance(name, str) and name.strip() else fallback


def _workspace_patterns(root: Path, root_package: dict) -> list[str] | None:
    """
    npm/yarn declare workspace member globs directly in package.json's
    "workspaces" field (a plain array, or {"packages": [...]} -- both real,
    seen in the wild). pnpm doesn't use package.json for this at all -- it's a
    separate pnpm-workspace.yaml with its own "packages" list. Returns None
    (not []) when nothing declares workspaces at all, distinct from a workspace
    repository whose glob patterns just happen to match nothing.
    """
    workspaces = root_package.get("workspaces")
    if isinstance(workspaces, dict):
        workspaces = workspaces.get("packages")
    if isinstance(workspaces, list) and all(isinstance(item, str) for item in workspaces):
        return workspaces
    pnpm_workspace = root / "pnpm-workspace.yaml"
    if pnpm_workspace.is_file():
        try:
            data = load_yaml(pnpm_workspace)
        except (OSError, ValueError):
            data = {}
        packages = data.get("packages")
        if isinstance(packages, list) and all(isinstance(item, str) for item in packages):
            return packages
    return None


def _resolve_workspace_pattern(root: Path, pattern: str) -> set[Path]:
    """
    Supports `*` and `**` (pathlib's own glob semantics) plus a leading `!` for
    negation, handled by the caller -- covers the large majority of real-world
    workspace declarations without chasing full glob-spec completeness (the
    same proportionality gradle.py's own regex-based, not a real Gradle-DSL-
    parser, detection already accepts). node_modules is always excluded even
    under a `**` pattern -- a nested dependency's own package.json is never a
    real workspace member, and a naive recursive glob would otherwise treat
    every installed package as one.
    """
    matches: set[Path] = set()
    for path in root.glob(pattern):
        if "node_modules" in path.parts:
            continue
        if path.is_dir() and (path / "package.json").is_file():
            matches.add(path.relative_to(root))
    return matches


def detect_npm_repository(root: Path) -> tuple[str, list[tuple[str, Path]]]:
    """
    Single-component unless the root package.json (or, for pnpm,
    pnpm-workspace.yaml) declares real workspace member globs -- in which case
    every glob match with its own package.json (minus any `!`-negated matches)
    becomes its own component, mirroring Gradle's multi-module shape. Resolving
    a glob pattern against the real filesystem is fully deterministic: unlike
    Gradle's `projectDir` remapping, there is nothing here a static parse can
    fail to resolve, so this never needs an AI fallback (see
    NpmAdapter.unresolved, which always returns []).
    """
    package_json = root / "package.json"
    if not package_json.is_file():
        raise ValueError(f"No package.json found under {root}")
    root_package = _read_json(package_json)
    repository_id = _package_name(root_package, root.name)

    patterns = _workspace_patterns(root, root_package)
    if not patterns:
        return repository_id, [(repository_id, Path("."))]

    included: set[Path] = set()
    excluded: set[Path] = set()
    for pattern in patterns:
        if pattern.startswith("!"):
            excluded |= _resolve_workspace_pattern(root, pattern[1:])
        else:
            included |= _resolve_workspace_pattern(root, pattern)
    members = sorted(included - excluded)
    if not members:
        return repository_id, [(repository_id, Path("."))]

    components: list[tuple[str, Path]] = [
        (_package_name(_read_json(root / relative / "package.json"), relative.name), relative)
        for relative in members
    ]
    return repository_id, components


def npm_component_structure(root: Path, relative: Path) -> dict[str, str]:
    component = root / relative
    candidates = {
        "source": component / "src",
        "tests": component / "__tests__",
        "documentation": component / "README.md",
        "build": component / "package.json",
    }
    paths = {
        key: str(path.relative_to(component))
        for key, path in candidates.items()
        if path.exists()
    }
    if "source" not in paths:
        # Next.js App Router (app/) and Pages Router (pages/) repositories without a
        # src/ directory keep routes directly at the component root. Mirrors
        # gradle.py's KMP source-set handling: use the source_ prefix so indexing.py's
        # search_roots filter (which only matches "source"/"tests" or the
        # source_*/tests_* prefixes) still picks these up.
        for name in ("app", "pages"):
            if (component / name).is_dir():
                paths[f"source_{name}"] = name
    return paths
