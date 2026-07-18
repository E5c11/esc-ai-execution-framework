from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from esc_exec.adapters import detect_build_system
from esc_exec.architecture import generate_architecture_profile
from esc_exec.architecture_lookup import (
    load_architecture_index, load_profile_doc_map, resolve_architecture_docs,
    stub_documents, suggest_profile_ids,
)
from esc_exec.dependencies import generate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import COMPONENT_MANIFEST, REPOSITORY_MANIFEST, generate_gradle_manifests
from esc_exec.registry import resolve_route
from esc_exec.task_context import generate_gradle_verification_profile
from esc_exec.workflow_bootstrap import bootstrap_workflow_inheritance
from esc_exec.yaml_io import load_yaml, write_yaml


ARCHITECTURE_FRAMEWORK_ID = "esc-ai-architecture-framework"


def import_project_profile(root: Path) -> dict[str, Any] | None:
    """
    Read a legacy context/project-profile.yaml if present, returning its
    frameworks/targets in the shape suggest_profile_ids expects. A repository that
    already has one needs little to no semantic questioning about architecture
    selection -- the answers already exist.
    """
    path = root / "context" / "project-profile.yaml"
    if not path.is_file():
        return None
    profile = load_yaml(path) or {}
    frameworks = profile.get("frameworks", {})
    targets = profile.get("targets", [])
    if isinstance(targets, str):
        targets = [targets]
    return {
        "frameworks": frameworks if isinstance(frameworks, dict) else {},
        "targets": targets if isinstance(targets, list) else [],
    }


def _load_profile_doc_map(registry_path: Path | None) -> dict[str, Any] | None:
    if registry_path is None:
        return None
    try:
        framework_root = resolve_route(registry_path, "frameworks", ARCHITECTURE_FRAMEWORK_ID)
        return load_profile_doc_map(framework_root)
    except (KeyError, FileNotFoundError, ValueError):
        return None


def _existing_profile_ids(root: Path, relative: Path) -> list[str]:
    manifest_path = root / relative / COMPONENT_MANIFEST
    if not manifest_path.is_file():
        return []
    existing = load_yaml(manifest_path) or {}
    return existing.get("architecture", {}).get("profile_ids", []) or []


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


def _architecture_signals(
    root: Path, components: list[tuple[str, Path]], registry_path: Path | None,
) -> tuple[dict[str, list[str]], list[dict[str, Any]]]:
    """
    For each component lacking an already-authored architecture.profile_ids:
    - if an imported project profile resolves a non-empty suggestion, offer it
      (no question needed -- the repository-level profile already answers this);
    - otherwise, if the architecture framework is resolvable at all, ask one bounded
      question for the frameworks/targets info needed to suggest profile_ids;
    - if the architecture framework can't be resolved, neither suggest nor ask --
      there's nothing safe to derive or offer.
    """
    suggestions: dict[str, list[str]] = {}
    questions: list[dict[str, Any]] = []

    profile_doc_map = _load_profile_doc_map(registry_path)
    imported = import_project_profile(root)
    repository_suggestion = (
        suggest_profile_ids(imported["frameworks"], imported["targets"], profile_doc_map)
        if profile_doc_map is not None and imported is not None
        else []
    )

    for component_id, relative in components:
        if _existing_profile_ids(root, relative):
            continue
        if imported is not None:
            if repository_suggestion:
                suggestions[component_id] = repository_suggestion
            continue
        if profile_doc_map is None:
            continue
        questions.append({
            "component_id": component_id, "field": "frameworks",
            "prompt": (
                f"Which frameworks does `{component_id}` use (field:value pairs such as "
                "network:ktor) and which targets (e.g. ios), if any? Used to suggest "
                "architecture.profile_ids."
            ),
        })
    return suggestions, questions


def analyze_repository(root: Path, registry_path: Path | None = None) -> dict[str, Any]:
    """
    Read-only repository onboarding analysis: detects build-system structure and
    classifies manifests as create/update/preserve/deprecate against it, without
    writing, creating, or modifying any file. registry_path is optional and only
    used to resolve the architecture framework for profile_ids suggestions/questions
    (see _architecture_signals) -- omitting it just means fewer/no suggestions.
    """
    root = root.resolve()
    repository_id, components, adapter = detect_build_system(root)
    repository_entry = _repository_file_entry(root, repository_id, components)
    component_entries, questions = _component_file_entries(root, components, adapter)
    deprecated_entries = _deprecated_component_entries(root, components)
    profile_id_suggestions, architecture_questions = _architecture_signals(root, components, registry_path)
    return {
        "schema_version": 1,
        "repository": {"id": repository_id, "type": adapter.repository_type},
        "input_digest": _input_digest(adapter.name, repository_id, components),
        "files": [repository_entry, *component_entries, *deprecated_entries],
        "semantic_questions": questions + architecture_questions,
        "existing_adoption": {
            "instructions_file": (root / "INSTRUCTIONS.md").is_file(),
            "workflows_directory": (root / ".esc-ai" / "workflows").is_dir(),
            "project_profile": (root / "context" / "project-profile.yaml").is_file(),
        },
        "profile_id_suggestions": profile_id_suggestions,
    }


def apply_onboarding_answers(
    root: Path,
    proposal: dict[str, Any],
    answers: dict[str, dict[str, Any]],
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """
    Apply human-provided answers to a repository, writing manifests for the first
    time -- analyze_repository stays strictly read-only; this is where onboarding
    starts changing the repository. answers is keyed by component_id:
    {"purpose": str, "frameworks": {field: value, ...}, "targets": [str, ...]}.

    Returns a dict (not just written paths) since callers must not silently miss
    stub documents or empty profile_id suggestions -- both are surfaced here rather
    than treated as fully specified.
    """
    root = root.resolve()
    repository_id, components, adapter = detect_build_system(root)
    if repository_id != proposal["repository"]["id"]:
        raise ValueError(
            f"proposal is for repository `{proposal['repository']['id']}` but `{root}` "
            f"now detects `{repository_id}` -- re-analyze before applying answers"
        )

    generated = generate_gradle_manifests(root)

    profile_doc_map = _load_profile_doc_map(registry_path)
    imported = import_project_profile(root)
    repository_suggestion = (
        suggest_profile_ids(imported["frameworks"], imported["targets"], profile_doc_map)
        if profile_doc_map is not None and imported is not None
        else []
    )

    written: list[Path] = list(generated)
    empty_profile_id_suggestions: list[str] = []
    for component_id, relative in components:
        manifest_path = root / relative / COMPONENT_MANIFEST
        manifest = load_yaml(manifest_path)
        answer = answers.get(component_id, {})
        if "purpose" in answer:
            manifest.setdefault("component", {})["purpose"] = answer["purpose"]

        if not manifest.get("architecture", {}).get("profile_ids"):
            frameworks = answer.get("frameworks", {})
            targets = answer.get("targets", [])
            attempted = bool(frameworks) or bool(targets) or imported is not None
            if (frameworks or targets) and profile_doc_map is not None:
                suggested = suggest_profile_ids(frameworks, targets, profile_doc_map)
            else:
                suggested = repository_suggestion
            if suggested:
                manifest["architecture"] = {"profile_ids": suggested}
            elif attempted:
                empty_profile_id_suggestions.append(component_id)

        write_yaml(manifest_path, manifest)

    # generate_gradle_verification_profile/generate_architecture_profile below both
    # resolve a component's manifest path through the root index, so it must exist
    # before they run. They also write back into that same manifest (paths.verification_profile/
    # architecture_profile), which is why the index gets regenerated *again* after
    # the loop -- otherwise it stays hashed against the pre-profile-generation bytes
    # and validate_indexes reports STALE immediately after onboarding.
    generate_indexes(root)

    stub_warnings: dict[str, list[str]] = {}
    missing_warnings: dict[str, list[str]] = {}
    architecture_index = None
    if registry_path is not None:
        try:
            framework_root = resolve_route(registry_path, "frameworks", ARCHITECTURE_FRAMEWORK_ID)
            architecture_index = load_architecture_index(framework_root)
        except (KeyError, FileNotFoundError, ValueError):
            architecture_index = None

    for component_id, relative in components:
        manifest_path = root / relative / COMPONENT_MANIFEST
        manifest = load_yaml(manifest_path)
        profile_ids = manifest.get("architecture", {}).get("profile_ids")
        if profile_ids and architecture_index is not None:
            documents, missing = resolve_architecture_docs(profile_ids, architecture_index)
            stubs = stub_documents(documents)
            if stubs:
                stub_warnings[component_id] = [document["id"] for document in stubs]
            if missing:
                missing_warnings[component_id] = missing

        if manifest.get("build", {}).get("system") == "gradle":
            verification_profile_path = manifest_path.parent / "esc-verification-profile.yaml"
            if not verification_profile_path.is_file():
                generate_gradle_verification_profile(root, component_id)
                written.append(verification_profile_path)

        architecture_profile_path = manifest_path.parent / "esc-architecture-profile.yaml"
        if not architecture_profile_path.is_file():
            generate_architecture_profile(root, component_id)
            written.append(architecture_profile_path)

    # Regenerate now that the profile generators above have finished mutating
    # manifests, so the index reflects final content instead of going stale the
    # instant it's written. The dependency graph is never generated during apply
    # at all otherwise, and only needs to run once, after manifests are final.
    written.extend(generate_indexes(root))
    written.append(generate_dependency_graph(root))

    repository_manifest = load_yaml(root / REPOSITORY_MANIFEST)
    workflow_inheritance = bootstrap_workflow_inheritance(root, repository_manifest)

    return {
        "written": [str(path.relative_to(root)) for path in written],
        "empty_profile_id_suggestions": empty_profile_id_suggestions,
        "stub_documents": stub_warnings,
        "missing_documents": missing_warnings,
        "workflow_inheritance": workflow_inheritance,
    }
