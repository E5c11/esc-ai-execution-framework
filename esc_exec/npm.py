from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

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


_TYPECHECK_SCRIPTS = ("typecheck", "type-check", "tsc")


def detect_package_manager(component_root: Path, repository_root: Path) -> str:
    """The package manager a project's lockfile names; ``npm`` when none is present."""
    for directory in (component_root, repository_root):
        if (directory / "pnpm-lock.yaml").is_file():
            return "pnpm"
        if (directory / "yarn.lock").is_file():
            return "yarn"
        if (directory / "bun.lock").is_file() or (directory / "bun.lockb").is_file():
            return "bun"
        if (directory / "package-lock.json").is_file():
            return "npm"
    return "npm"


def _run_script(manager: str, component_path: str, script: str, extra: list[str] | None = None) -> list[str]:
    """``<manager> run <script> [args]`` in the component's directory."""
    flags = {"npm": ["--prefix"], "pnpm": ["--dir"], "yarn": ["--cwd"], "bun": ["--cwd"]}[manager]
    command = [manager] if component_path in {"", "."} else [manager, *flags, component_path]
    command += ["run", script]
    if extra:
        command += ["--", *extra] if manager == "npm" else list(extra)
    return command


def _exec_tool(manager: str, tool: str, *arguments: str) -> list[str]:
    prefix = {"npm": ["npx", "--no-install"], "pnpm": ["pnpm", "exec"], "yarn": ["yarn"], "bun": ["bunx"]}[manager]
    return [*prefix, tool, *arguments]


def _test_runner_arguments(script: str) -> list[str]:
    """A bare ``vitest`` watches and never exits; verification needs a run that ends."""
    if re.search(r"\bvitest\b", script) and not re.search(r"(^|\s)(run|--run|--watch[= ]false)(\s|$)", script):
        return ["run"]
    return []


def npm_verification_gates(
    repository_root: Path, component_id: str, component_path: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[str]]:
    """The four progressive gates for an npm component, built from its own ``package.json`` scripts.

    Fails closed: a component with no ``test`` script raises, because a profile whose test gate is empty
    would report "verified" for code nothing ran. A missing type-check, lint or build is recorded in the
    returned notes instead, since a project may legitimately have no such step, but the reader must see
    that nothing checks it.
    """
    component_root = repository_root / component_path
    manifest = _read_json(component_root / "package.json")
    scripts = manifest.get("scripts") if isinstance(manifest.get("scripts"), dict) else {}
    if "test" not in scripts:
        raise ValueError(
            f"{component_id}: package.json has no `test` script, so there is nothing to verify; "
            "add one (and keep it non-interactive) before generating a verification profile"
        )
    manager = detect_package_manager(component_root, repository_root)
    path = component_path or "."
    notes: list[str] = []
    runner = _test_runner_arguments(str(scripts["test"]))
    component_checks: list[dict[str, Any]] = []

    typecheck = next((name for name in _TYPECHECK_SCRIPTS if name in scripts), None)
    dependencies: dict[str, Any] = {}
    for section in ("devDependencies", "dependencies"):
        if isinstance(manifest.get(section), dict):
            dependencies.update(manifest[section])
    if typecheck:
        component_checks.append({"id": f"{component_id}-typecheck", "command": _run_script(manager, path, typecheck)})
    elif path == "." and (component_root / "tsconfig.json").is_file() and "typescript" in dependencies:
        component_checks.append({"id": f"{component_id}-typecheck", "command": _exec_tool(manager, "tsc", "--noEmit")})
    elif (component_root / "tsconfig.json").is_file():
        notes.append("no `typecheck` script: types are not verified (add one, or rely on `build` in the final gate)")

    if "lint" in scripts:
        component_checks.append({"id": f"{component_id}-lint", "command": _run_script(manager, path, "lint")})
    else:
        notes.append("no `lint` script: linting is not verified (`next build` does not lint from Next.js 16)")

    component_checks.append({"id": f"{component_id}-tests", "command": _run_script(manager, path, "test", runner or None)})

    final_checks: list[dict[str, Any]] = []
    root_scripts = _read_json(repository_root / "package.json").get("scripts")
    if path != "." and isinstance(root_scripts, dict) and "test" in root_scripts:
        final_checks.append({"id": "repository-tests", "command": _run_script(manager, ".", "test")})
    if "build" in scripts:
        final_checks.append({"id": f"{component_id}-build", "command": _run_script(manager, path, "build")})
    else:
        notes.append("no `build` script: the final gate verifies nothing beyond the tests")

    gates = {
        "focused": [{
            "id": f"{component_id}-focused-tests",
            "command": _run_script(manager, path, "test", [*runner, "{test_filter}"]),
            "requires": ["test_filter"],
        }],
        "component": component_checks,
        "impact": [],
        "final": final_checks,
    }
    return gates, notes
