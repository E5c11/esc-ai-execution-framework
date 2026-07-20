from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.framework_descriptor import check_framework_compatibility
from esc_exec.adapters import detect_build_system
from esc_exec.gradle import component_structure, detect_gradle_repository, gradle_project_paths
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.npm import detect_npm_repository, npm_component_structure
from esc_exec.registry import RENAMED_FRAMEWORK_IDS
from esc_exec.yaml_io import load_yaml, write_yaml


REPOSITORY_MANIFEST = "esc-execution.yaml"
COMPONENT_MANIFEST = "esc-component.yaml"

# build.system values a generated component manifest is allowed to declare --
# one entry per registered BuildSystemAdapter (esc_exec/adapters.py).
SUPPORTED_BUILD_SYSTEMS = {"gradle", "npm"}

# Every escape-ai-owned generated/managed file lives under this repository-local
# directory, keyed by stable component ID rather than mirroring a component's real
# (expected-to-change) filesystem path. See the plan's "Repository-local Escape AI
# directory" section: escape-ai always resolves a repository through the
# machine-local registry by ID first, then reads a conventional relative path under
# an already-known root -- it never discovers a repository by scanning for these
# files, so nothing requires them to sit at repository/component root.
ESC_AI_DIR = ".esc-ai"
COMPONENTS_DIR = "components"


def repository_manifest_path(root: Path) -> Path:
    """Absolute path to the repository manifest's storage location."""
    return root / ESC_AI_DIR / REPOSITORY_MANIFEST


def repository_manifest_relative_path() -> str:
    """The repository-root-relative path to the repository manifest."""
    return f"{ESC_AI_DIR}/{REPOSITORY_MANIFEST}"


def component_manifest_dir(root: Path, component_id: str) -> Path:
    """
    The manifest bundle directory for a component, flat and keyed by stable
    component ID -- not mirroring `component["path"]` (the component's real,
    expected-to-change source location), which stays `repository_root /
    component["path"]` and is completely unaffected by where the manifest bundle
    itself is stored.
    """
    return root / ESC_AI_DIR / COMPONENTS_DIR / component_id


def component_manifest_path(root: Path, component_id: str) -> Path:
    return component_manifest_dir(root, component_id) / COMPONENT_MANIFEST


def component_manifest_relative_path(component_id: str) -> str:
    """The repository-root-relative path stored in components[].manifest."""
    return f"{ESC_AI_DIR}/{COMPONENTS_DIR}/{component_id}/{COMPONENT_MANIFEST}"


def _merge_generated(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    for key, value in generated.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_generated(result[key], value)
        else:
            result[key] = value
    return result


def _architecture_selector_errors(data: dict[str, Any]) -> list[str]:
    architecture = data.get("architecture")
    if architecture is None:
        return []
    if not isinstance(architecture, dict) or set(architecture) - {"profile_ids"}:
        return ["architecture must be a mapping with only a profile_ids key"]
    profile_ids = architecture.get("profile_ids")
    if not isinstance(profile_ids, list) or not profile_ids or not all(isinstance(item, str) and item.strip() for item in profile_ids):
        return ["architecture.profile_ids must be a non-empty list of non-empty strings"]
    return []


def generate_gradle_manifests(
    root: Path, repository_id: str | None = None, components: list[tuple[str, Path]] | None = None,
) -> list[Path]:
    """
    repository_id/components are optional overrides -- when omitted (every
    existing call site), behavior is unchanged: detect fresh via
    detect_gradle_repository. A caller that already computed a final component
    list (e.g. onboarding merging in Tier 2 AI-resolved components and applying
    exclusions -- see plan/active/generic-multi-component-detection.md) passes it
    through here instead of this function silently re-deriving Tier 1-only
    components and ignoring that work.
    """
    root = root.resolve()
    if repository_id is None or components is None:
        detected_id, detected_components = detect_gradle_repository(root)
        repository_id = repository_id if repository_id is not None else detected_id
        components = components if components is not None else detected_components
    project_paths = gradle_project_paths(root)
    repository_path = repository_manifest_path(root)
    existing_repository = load_yaml(repository_path) if repository_path.exists() else {}
    generated_repository = {
        "schema_version": 1,
        "repository": {
            "id": repository_id,
            "type": "gradle-multi-project",
        },
        "components": [
            {"id": component_id, "manifest": component_manifest_relative_path(component_id)}
            for component_id, relative in components
        ],
        "generation": {
            "generator": "esc-exec",
            "sources": ["settings.gradle.kts" if (root / "settings.gradle.kts").exists() else "settings.gradle"],
        },
    }
    write_yaml(repository_path, _merge_generated(existing_repository, generated_repository))
    written = [repository_path]

    for component_id, relative in components:
        manifest_path = component_manifest_path(root, component_id)
        existing = load_yaml(manifest_path) if manifest_path.exists() else {}
        # project_paths only knows about identifiers this repository's settings
        # file actually declared via include(...) -- an AI-resolved component
        # (not statically parseable at all, see gradle.py's unresolved_gradle_
        # includes) falls back to the directory-derived reconstruction, the best
        # available guess when there's no real declared project path to read.
        project_path = project_paths.get(component_id, ":" + ":".join(relative.parts))
        generated = {
            "schema_version": 1,
            "component": {
                "id": component_id,
                "type": "gradle-module",
                "path": str(relative),
            },
            "build": {
                "system": "gradle",
                "project": project_path,
            },
            "paths": component_structure(root, relative),
            "generation": {
                "generator": "esc-exec",
                "sources": ["settings.gradle.kts" if (root / "settings.gradle.kts").exists() else "settings.gradle"],
            },
        }
        write_yaml(manifest_path, _merge_generated(existing, generated))
        written.append(manifest_path)
    return written


def generate_npm_manifests(
    root: Path, repository_id: str | None = None, components: list[tuple[str, Path]] | None = None,
) -> list[Path]:
    """See generate_gradle_manifests' docstring -- same optional-override contract."""
    root = root.resolve()
    if repository_id is None or components is None:
        detected_id, detected_components = detect_npm_repository(root)
        repository_id = repository_id if repository_id is not None else detected_id
        components = components if components is not None else detected_components
    repository_path = repository_manifest_path(root)
    existing_repository = load_yaml(repository_path) if repository_path.exists() else {}
    generated_repository = {
        "schema_version": 1,
        "repository": {
            "id": repository_id,
            "type": "npm-package",
        },
        "components": [
            {"id": component_id, "manifest": component_manifest_relative_path(component_id)}
            for component_id, relative in components
        ],
        "generation": {
            "generator": "esc-exec",
            "sources": ["package.json"],
        },
    }
    write_yaml(repository_path, _merge_generated(existing_repository, generated_repository))
    written = [repository_path]

    for component_id, relative in components:
        manifest_path = component_manifest_path(root, component_id)
        existing = load_yaml(manifest_path) if manifest_path.exists() else {}
        generated = {
            "schema_version": 1,
            "component": {
                "id": component_id,
                "type": "npm-package",
                "path": str(relative),
            },
            "build": {
                "system": "npm",
            },
            "paths": npm_component_structure(root, relative),
            "generation": {
                "generator": "esc-exec",
                "sources": ["package.json"],
            },
        }
        write_yaml(manifest_path, _merge_generated(existing, generated))
        written.append(manifest_path)
    return written


def validate_repository(root: Path, registry_path: Path | None = None) -> list[ValidationResult]:
    root = root.resolve()
    repository_path = repository_manifest_path(root)
    if not repository_path.exists():
        return [ValidationResult(
            ManifestState.INCOMPLETE,
            str(repository_path),
            [f"Repository manifest is missing. Run: esc-exec manifest generate {root}"],
        )]
    try:
        repository = load_yaml(repository_path)
    except (OSError, ValueError) as exc:
        return [ValidationResult(ManifestState.INVALID, str(repository_path), [str(exc)])]

    results: list[ValidationResult] = []
    repo_messages: list[str] = []
    if repository.get("schema_version") != 1:
        repo_messages.append("schema_version must be 1")
    identity = repository.get("repository")
    if not isinstance(identity, dict):
        repo_messages.append("repository must be a mapping")
    else:
        for field in ("id", "type"):
            if not isinstance(identity.get(field), str) or not identity[field].strip():
                repo_messages.append(f"repository.{field} must be a non-empty string")
    components = repository.get("components")
    if not isinstance(components, list) or not components:
        repo_messages.append("components must be a non-empty list")
        components = []
    repo_messages.extend(_architecture_selector_errors(repository))
    results.append(ValidationResult(
        ManifestState.INVALID if repo_messages else ManifestState.VALID,
        str(repository_path),
        repo_messages,
    ))

    frameworks = repository.get("frameworks")
    if isinstance(frameworks, dict):
        renamed = sorted(fid for fid in frameworks if fid in RENAMED_FRAMEWORK_IDS)
        if renamed and results[0].state == ManifestState.VALID:
            results[0] = ValidationResult(
                ManifestState.STALE,
                str(repository_path),
                [
                    f"frameworks references renamed framework ID `{fid}`; "
                    f"update to `{RENAMED_FRAMEWORK_IDS[fid]}`."
                    for fid in renamed
                ],
            )

    declared_paths: set[str] = set()
    for item in components:
        if not isinstance(item, dict) or not isinstance(item.get("manifest"), str):
            results.append(ValidationResult(
                ManifestState.INVALID,
                str(repository_path),
                ["Every components item must contain string id and manifest fields"],
            ))
            continue
        declared_paths.add(item["manifest"])
        manifest_path = root / item["manifest"]
        results.append(validate_component(root, manifest_path, expected_id=item.get("id")))

    try:
        _, detected, _ = detect_build_system(root)
        detected_paths = {component_manifest_relative_path(component_id) for component_id, _ in detected}
        missing = sorted(detected_paths - declared_paths)
        if missing:
            results[0] = ValidationResult(
                ManifestState.STALE,
                str(repository_path),
                [f"Detected undeclared component manifest: {path}" for path in missing],
            )
    except ValueError:
        pass

    if registry_path is not None:
        compatibility = check_framework_compatibility(repository, registry_path)
        if compatibility.state != ManifestState.VALID:
            precedence = {ManifestState.VALID: 0, ManifestState.INCOMPLETE: 1, ManifestState.STALE: 2, ManifestState.INVALID: 3}
            escalated = max(results[0].state, compatibility.state, key=precedence.__getitem__)
            results[0] = ValidationResult(
                escalated,
                str(repository_path),
                results[0].messages + compatibility.messages,
            )
    return results


def validate_component(root: Path, path: Path, expected_id: Any = None) -> ValidationResult:
    if not path.exists():
        return ValidationResult(
            ManifestState.INCOMPLETE,
            str(path),
            ["Declared component manifest is missing; regenerate repository manifests."],
        )
    try:
        data = load_yaml(path)
    except (OSError, ValueError) as exc:
        return ValidationResult(ManifestState.INVALID, str(path), [str(exc)])
    invalid: list[str] = []
    incomplete: list[str] = []
    if data.get("schema_version") != 1:
        invalid.append("schema_version must be 1")
    component = data.get("component")
    if not isinstance(component, dict):
        invalid.append("component must be a mapping")
    else:
        for field in ("id", "type", "path"):
            if not isinstance(component.get(field), str) or not component[field].strip():
                invalid.append(f"component.{field} must be a non-empty string")
        if expected_id and component.get("id") != expected_id:
            invalid.append(f"component.id must match declared id `{expected_id}`")
        if not isinstance(component.get("purpose"), str) or not component.get("purpose", "").strip():
            incomplete.append("component.purpose requires human-authored semantic input")
        component_root = root / component.get("path", "")
        if component.get("path") and not component_root.is_dir():
            return ValidationResult(
                ManifestState.STALE,
                str(path),
                [f"component.path points to missing directory: {component.get('path')}"],
            )
    build = data.get("build")
    if not isinstance(build, dict) or build.get("system") not in SUPPORTED_BUILD_SYSTEMS:
        invalid.append(f"build.system must be one of {sorted(SUPPORTED_BUILD_SYSTEMS)}")
    invalid.extend(_architecture_selector_errors(data))
    if invalid:
        return ValidationResult(ManifestState.INVALID, str(path), invalid + incomplete)
    if incomplete:
        return ValidationResult(ManifestState.INCOMPLETE, str(path), incomplete)
    return ValidationResult(ManifestState.VALID, str(path), [])


def overall_exit_code(results: list[ValidationResult]) -> int:
    precedence = {
        ManifestState.VALID: 0,
        ManifestState.INCOMPLETE: 2,
        ManifestState.STALE: 3,
        ManifestState.INVALID: 1,
    }
    if any(result.state == ManifestState.INVALID for result in results):
        return 1
    return max((precedence[result.state] for result in results), default=0)

