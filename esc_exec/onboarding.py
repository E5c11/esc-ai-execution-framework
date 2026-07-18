from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.manifests import COMPONENT_MANIFEST, REPOSITORY_MANIFEST
from esc_exec.yaml_io import load_yaml


def _input_digest(adapter_name: str, repository_id: str, components: list[tuple[str, Path]]) -> str:
    lines = [adapter_name, repository_id] + [
        f"{component_id}:{relative}" for component_id, relative in sorted(components)
    ]
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _repository_file_entry(root: Path, repository_id: str, components: list[tuple[str, Path]]) -> dict[str, Any]:
    detected_ids = sorted(component_id for component_id, _ in components)
    path = root / REPOSITORY_MANIFEST
    if not path.exists():
        return {
            "path": REPOSITORY_MANIFEST,
            "action": "create",
            "evidence": (
                f"No {REPOSITORY_MANIFEST} found; detected repository `{repository_id}` "
                f"with components: {', '.join(detected_ids) or 'none'}."
            ),
        }
    existing = load_yaml(path)
    existing_id = existing.get("repository", {}).get("id")
    existing_ids = sorted(
        item.get("id") for item in existing.get("components", []) if isinstance(item, dict)
    )
    if existing_id == repository_id and existing_ids == detected_ids:
        return {
            "path": REPOSITORY_MANIFEST,
            "action": "preserve",
            "evidence": "Existing manifest matches detected structure.",
        }
    return {
        "path": REPOSITORY_MANIFEST,
        "action": "update",
        "evidence": (
            f"Detected repository `{repository_id}` with components {detected_ids}; "
            f"existing manifest declares `{existing_id}` with {existing_ids}."
        ),
    }


def _component_file_entries(
    root: Path, components: list[tuple[str, Path]], adapter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for component_id, relative in components:
        manifest_path = root / relative / COMPONENT_MANIFEST
        relative_manifest = str(relative / COMPONENT_MANIFEST)
        if not manifest_path.exists():
            entries.append({
                "path": relative_manifest,
                "action": "create",
                "evidence": f"No manifest found for detected component `{component_id}` at `{relative}`.",
            })
            questions.append({
                "component_id": component_id, "field": "purpose",
                "prompt": f"What is the purpose of component `{component_id}`?",
            })
            continue
        existing = load_yaml(manifest_path)
        component = existing.get("component", {})
        detected_path = str(relative)
        if component.get("type") == adapter.component_type and component.get("path") == detected_path:
            entries.append({
                "path": relative_manifest, "action": "preserve",
                "evidence": "Existing manifest matches detected structure.",
            })
        else:
            entries.append({
                "path": relative_manifest,
                "action": "update",
                "evidence": (
                    f"Detected type `{adapter.component_type}` at path `{detected_path}`; "
                    f"existing manifest declares type `{component.get('type')}` at path `{component.get('path')}`."
                ),
            })
        purpose = component.get("purpose")
        if not isinstance(purpose, str) or not purpose.strip():
            questions.append({
                "component_id": component_id, "field": "purpose",
                "prompt": f"What is the purpose of component `{component_id}`?",
            })
    return entries, questions


def _deprecated_component_entries(root: Path, components: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    repository_path = root / REPOSITORY_MANIFEST
    if not repository_path.exists():
        return []
    existing = load_yaml(repository_path)
    detected_manifests = {str(relative / COMPONENT_MANIFEST) for _, relative in components}
    entries: list[dict[str, Any]] = []
    for item in existing.get("components", []):
        if isinstance(item, dict) and isinstance(item.get("manifest"), str) and item["manifest"] not in detected_manifests:
            entries.append({
                "path": item["manifest"],
                "action": "deprecate",
                "evidence": f"Component `{item.get('id')}` is declared but no longer detected in the build system.",
            })
    return entries


def analyze_repository(root: Path) -> dict[str, Any]:
    """
    Read-only repository onboarding analysis: detects build-system structure and
    classifies manifests as create/update/preserve/deprecate against it, without
    writing, creating, or modifying any file.
    """
    root = root.resolve()
    repository_id, components, adapter = detect_build_system(root)
    repository_entry = _repository_file_entry(root, repository_id, components)
    component_entries, questions = _component_file_entries(root, components, adapter)
    deprecated_entries = _deprecated_component_entries(root, components)
    return {
        "schema_version": 1,
        "repository": {"id": repository_id, "type": adapter.repository_type},
        "input_digest": _input_digest(adapter.name, repository_id, components),
        "files": [repository_entry, *component_entries, *deprecated_entries],
        "semantic_questions": questions,
        "existing_adoption": {
            "instructions_file": (root / "INSTRUCTIONS.md").is_file(),
            "workflows_directory": (root / ".esc-ai" / "workflows").is_dir(),
            "project_profile": (root / "context" / "project-profile.yaml").is_file(),
        },
    }
