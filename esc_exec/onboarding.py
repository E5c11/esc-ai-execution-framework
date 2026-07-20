from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from esc_exec.adapters import BuildSystemAdapter, component_id_from_identifier, detect_build_system
from esc_exec.architecture import generate_architecture_profile
from esc_exec.architecture_lookup import (
    load_architecture_index, load_profile_doc_map, resolve_architecture_docs,
    stub_documents, suggest_profile_ids,
)
from esc_exec.dependencies import detect_gradle_frameworks_and_targets, generate_dependency_graph
from esc_exec.indexing import generate_indexes
from esc_exec.manifests import (
    component_manifest_path, component_manifest_relative_path, generate_gradle_manifests,
    generate_npm_manifests, repository_manifest_path, repository_manifest_relative_path,
)
from esc_exec.registry import resolve_route
from esc_exec.task_context import generate_gradle_verification_profile
from esc_exec.workflow_bootstrap import INSTRUCTIONS_PATH, bootstrap_workflow_inheritance
from esc_exec.yaml_io import load_yaml, write_yaml


ARCHITECTURE_FRAMEWORK_ID = "esc-ai-architecture-framework"

# One manifest generator per registered BuildSystemAdapter (esc_exec/adapters.py) --
# apply_onboarding_answers dispatches through this instead of hardcoding Gradle so
# onboarding's write side is actually generic, not just its detection side.
MANIFEST_GENERATORS = {
    "gradle": generate_gradle_manifests,
    "npm": generate_npm_manifests,
}


def _merged_components(
    root: Path, components: list[tuple[str, Path]], adapter: BuildSystemAdapter,
    extra_resolved: dict[str, str] | None = None, extra_excluded: set[str] | None = None,
) -> tuple[list[tuple[str, Path]], dict[str, str], set[str]]:
    """
    See plan/active/generic-multi-component-detection.md design sections 3-6.
    Extends Tier 1-detected `components` with any AI-resolved identifier this
    repository's manifest already remembers (`resolved_components`, persisted by a
    past apply_onboarding_answers call) plus any new ones this call is confirming
    (`extra_resolved`) -- for whichever identifiers `adapter.unresolved(root)`
    still reports -- so a confirmed AI answer never needs to be re-asked, and never
    gets silently misclassified as a removed component on a later analyze just
    because Tier 1 alone still can't resolve it. Then drops anything in
    `excluded_components` (persisted) or `extra_excluded` (new this call) --
    exclusion always wins over both Tier 1 detection and a persisted AI
    resolution, and applies uniformly regardless of which tier found a component.

    Returns (filtered components, full resolved map, full excluded set) -- the
    latter two are what a caller confirming new entries should persist back into
    the repository manifest so a future call (even with no override arguments at
    all) reflects them automatically.
    """
    manifest_path = repository_manifest_path(root)
    existing = load_yaml(manifest_path) if manifest_path.exists() else {}
    resolved_map = dict(existing.get("resolved_components") or {})
    resolved_map.update(extra_resolved or {})
    excluded = set(existing.get("excluded_components") or [])
    excluded |= extra_excluded or set()

    merged = list(components)
    seen_ids = {component_id for component_id, _ in merged}
    for identifier in adapter.unresolved(root):
        relative = resolved_map.get(identifier)
        if not relative or not (root / relative).is_dir():
            continue
        component_id = component_id_from_identifier(identifier)
        if component_id in seen_ids:
            continue
        merged.append((component_id, Path(relative)))
        seen_ids.add(component_id)

    filtered = [(component_id, relative) for component_id, relative in merged if component_id not in excluded]
    return filtered, resolved_map, excluded


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


def _existing_profile_ids(root: Path, component_id: str) -> list[str]:
    manifest_path = component_manifest_path(root, component_id)
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
    path = repository_manifest_path(root)
    relative_manifest = repository_manifest_relative_path()
    if not path.exists():
        return {
            "path": relative_manifest,
            "action": "create",
            "evidence": (
                f"No {relative_manifest} found; detected repository `{repository_id}` "
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
            "path": relative_manifest,
            "action": "preserve",
            "evidence": "Existing manifest matches detected structure.",
        }
    return {
        "path": relative_manifest,
        "action": "update",
        "evidence": (
            f"Detected repository `{repository_id}` with components {detected_ids}; "
            f"existing manifest declares `{existing_id}` with {existing_ids}."
        ),
    }


def _purpose_question(component_id: str) -> dict[str, Any]:
    return {
        "component_id": component_id, "field": "purpose",
        "prompt": (
            f"What is the purpose of component `{component_id}`?\n"
            '    Example: "Handles user authentication and session tokens"'
        ),
    }


def _component_file_entries(
    root: Path, components: list[tuple[str, Path]], adapter,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    entries: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    for component_id, relative in components:
        manifest_path = component_manifest_path(root, component_id)
        relative_manifest = component_manifest_relative_path(component_id)
        if not manifest_path.exists():
            entries.append({
                "path": relative_manifest,
                "action": "create",
                "evidence": f"No manifest found for detected component `{component_id}` at `{relative}`.",
            })
            questions.append(_purpose_question(component_id))
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
            questions.append(_purpose_question(component_id))
    return entries, questions


def _deprecated_component_entries(root: Path, components: list[tuple[str, Path]]) -> list[dict[str, Any]]:
    repository_path = repository_manifest_path(root)
    if not repository_path.exists():
        return []
    existing = load_yaml(repository_path)
    detected_manifests = {component_manifest_relative_path(component_id) for component_id, _ in components}
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
    - otherwise, try Tier 1 static detection (see
      plan/onboarding-answer-detection-and-suggestion.md) against the component's own
      build file -- if that resolves a non-empty suggestion, offer it, no question
      needed either;
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
        if _existing_profile_ids(root, component_id):
            continue
        if imported is not None:
            if repository_suggestion:
                suggestions[component_id] = repository_suggestion
            continue
        if profile_doc_map is None:
            continue
        detected_frameworks, detected_targets = detect_gradle_frameworks_and_targets(
            root / relative / "build.gradle.kts"
        )
        detected_suggestion = (
            suggest_profile_ids(detected_frameworks, detected_targets, profile_doc_map)
            if detected_frameworks or detected_targets
            else []
        )
        if detected_suggestion:
            suggestions[component_id] = detected_suggestion
            continue
        questions.append({
            "component_id": component_id, "field": "frameworks",
            "prompt": (
                f"Which frameworks does `{component_id}` use? (optional -- press Enter to skip)\n"
                "    Format: name:value pairs, comma-separated. Used to suggest architecture.profile_ids.\n"
                "    Example: network:ktor, database:room, di:hilt"
            ),
        })
    return suggestions, questions


def analyze_repository(
    root: Path, registry_path: Path | None = None, extra_resolved_components: dict[str, str] | None = None,
) -> dict[str, Any]:
    """
    Read-only repository onboarding analysis: detects build-system structure and
    classifies manifests as create/update/preserve/deprecate against it, without
    writing, creating, or modifying any file. registry_path is optional and only
    used to resolve the architecture framework for profile_ids suggestions/questions
    (see _architecture_signals) -- omitting it just means fewer/no suggestions.

    extra_resolved_components carries this session's not-yet-persisted Tier 2
    AI-resolved identifiers (see _merged_components) -- omitted, analysis still
    picks up whatever was already persisted from a past apply, just not a
    brand-new resolution from earlier in the same interactive session that
    hasn't been applied (and therefore persisted) yet.
    """
    root = root.resolve()
    repository_id, components, adapter = detect_build_system(root)
    components, _resolved_map, _excluded = _merged_components(
        root, components, adapter, extra_resolved=extra_resolved_components,
    )
    repository_entry = _repository_file_entry(root, repository_id, components)
    component_entries, questions = _component_file_entries(root, components, adapter)
    deprecated_entries = _deprecated_component_entries(root, components)
    profile_id_suggestions, architecture_questions = _architecture_signals(root, components, registry_path)
    return {
        "schema_version": 1,
        "repository": {"id": repository_id, "type": adapter.repository_type},
        "input_digest": _input_digest(adapter.name, repository_id, components),
        "components": [{"id": component_id, "path": str(relative)} for component_id, relative in components],
        "files": [repository_entry, *component_entries, *deprecated_entries],
        "semantic_questions": questions + architecture_questions,
        "existing_adoption": {
            "instructions_file": (root / INSTRUCTIONS_PATH).is_file(),
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
    resolved_components: dict[str, str] | None = None,
    excluded_component_ids: list[str] | None = None,
) -> dict[str, Any]:
    """
    Apply human-provided answers to a repository, writing manifests for the first
    time -- analyze_repository stays strictly read-only; this is where onboarding
    starts changing the repository. answers is keyed by component_id:
    {"purpose": str, "frameworks": {field: value, ...}, "targets": [str, ...]}.

    resolved_components/excluded_component_ids carry this session's newly
    confirmed Tier 2 AI resolutions and user exclusions (see
    plan/active/generic-multi-component-detection.md design sections 3-6) -- both
    get merged with whatever was already persisted from a past apply
    (_merged_components) and written back into the repository manifest's
    resolved_components/excluded_components fields, so a future analyze/apply
    (even with no override arguments at all) reflects them automatically without
    re-asking the AI or re-offering an already-excluded component.

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
    components, resolved_map, excluded = _merged_components(
        root, components, adapter,
        extra_resolved=resolved_components, extra_excluded=set(excluded_component_ids or []),
    )

    try:
        manifest_generator = MANIFEST_GENERATORS[adapter.name]
    except KeyError:
        raise ValueError(f"No manifest generator registered for build-system adapter `{adapter.name}`") from None
    generated = manifest_generator(root, repository_id, components)

    if resolved_map or excluded:
        repository_path = repository_manifest_path(root)
        repository_manifest = load_yaml(repository_path)
        if resolved_map:
            repository_manifest["resolved_components"] = resolved_map
        if excluded:
            repository_manifest["excluded_components"] = sorted(excluded)
        write_yaml(repository_path, repository_manifest)

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
        manifest_path = component_manifest_path(root, component_id)
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
        manifest_path = component_manifest_path(root, component_id)
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

    repository_manifest = load_yaml(repository_manifest_path(root))
    workflow_inheritance = bootstrap_workflow_inheritance(root, repository_manifest)

    return {
        "written": [str(path.relative_to(root)) for path in written],
        "empty_profile_id_suggestions": empty_profile_id_suggestions,
        "stub_documents": stub_warnings,
        "missing_documents": missing_warnings,
        "workflow_inheritance": workflow_inheritance,
    }
