from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.gradle import component_structure, detect_gradle_repository
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.registry import RENAMED_FRAMEWORK_IDS
from esc_exec.yaml_io import load_yaml, write_yaml


REPOSITORY_MANIFEST = "esc-execution.yaml"
COMPONENT_MANIFEST = "esc-component.yaml"


def _merge_generated(existing: dict[str, Any], generated: dict[str, Any]) -> dict[str, Any]:
    result = dict(existing)
    for key, value in generated.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge_generated(result[key], value)
        else:
            result[key] = value
    return result


def generate_gradle_manifests(root: Path) -> list[Path]:
    root = root.resolve()
    repository_id, components = detect_gradle_repository(root)
    repository_path = root / REPOSITORY_MANIFEST
    existing_repository = load_yaml(repository_path) if repository_path.exists() else {}
    generated_repository = {
        "schema_version": 1,
        "repository": {
            "id": repository_id,
            "type": "gradle-multi-project",
        },
        "components": [
            {"id": component_id, "manifest": str(relative / COMPONENT_MANIFEST)}
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
        manifest_path = root / relative / COMPONENT_MANIFEST
        existing = load_yaml(manifest_path) if manifest_path.exists() else {}
        generated = {
            "schema_version": 1,
            "component": {
                "id": component_id,
                "type": "gradle-module",
                "path": str(relative),
            },
            "build": {
                "system": "gradle",
                "project": ":" + ":".join(relative.parts),
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


def validate_repository(root: Path) -> list[ValidationResult]:
    root = root.resolve()
    repository_path = root / REPOSITORY_MANIFEST
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
        _, detected = detect_gradle_repository(root)
        detected_paths = {str(relative / COMPONENT_MANIFEST) for _, relative in detected}
        missing = sorted(detected_paths - declared_paths)
        if missing:
            results[0] = ValidationResult(
                ManifestState.STALE,
                str(repository_path),
                [f"Detected undeclared component manifest: {path}" for path in missing],
            )
    except ValueError:
        pass
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
    if not isinstance(build, dict) or build.get("system") != "gradle":
        invalid.append("build.system must be `gradle` for a generated Gradle component")
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

