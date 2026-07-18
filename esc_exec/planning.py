from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.checkpoints import TASK_ID
from esc_exec.indexing import INDEX_FILE, Match, match_components
from esc_exec.json_io import load_json
from esc_exec.manifests import ESC_AI_DIR
from esc_exec.registry import resolve_route
from esc_exec.yaml_io import load_yaml, write_yaml


WORK_TYPES = ("feature", "fix", "refactor", "maintenance", "investigation")


def route_objective(repository: Path, objective: str, max_matches: int = 5) -> list[Match]:
    """Thin wrapper over the existing keyword-routing index lookup (esc-exec index
    match) -- planning does not reimplement matching, it reuses it."""
    return match_components(repository, objective)[:max_matches]


def planning_questions(repository_matches: dict[str, list[Match]]) -> list[dict[str, Any]]:
    """
    Typed questions for what genuinely cannot be derived from routing alone: which
    of the suggested (or other) components this initiative actually touches per
    repository, plus the product decisions the plan's Planning conversation section
    names as needing a human -- scope boundaries, completion conditions, rollout
    needs. This is deliberately not a live conversation (see the tracking doc for
    why): a bounded, typed question set is what onboarding already does successfully
    for the same class of "can't be derived" problem.
    """
    questions: list[dict[str, Any]] = []
    for repository_id, matches in repository_matches.items():
        suggested = ", ".join(match.component_id for match in matches) or "none matched"
        questions.append({
            "field": "components", "repository": repository_id,
            "prompt": f"Which components in `{repository_id}` does this touch? (comma-separated ids; suggested: {suggested})",
        })
    questions.append({
        "field": "scope_boundary",
        "prompt": "What is explicitly out of scope for this initiative?",
    })
    questions.append({
        "field": "completion_conditions",
        "prompt": "What conditions must be true for this initiative to be complete? (comma-separated)",
    })
    questions.append({
        "field": "rollout_needs",
        "prompt": "Any rollout/deployment needs? (optional, blank if none)",
    })
    return questions


def _load_index(repository: Path) -> dict[str, Any]:
    return load_json(repository / ESC_AI_DIR / INDEX_FILE)


def _validate_components(repository: Path, components: list[str]) -> None:
    index = _load_index(repository)
    known = {item["id"] for item in index["components"]}
    missing = sorted(set(components) - known)
    if missing:
        raise ValueError(f"components not in the repository index: {', '.join(missing)}")


def _architecture_doc_ids(repository: Path, index: dict[str, Any], components: list[str]) -> list[str]:
    ids: list[str] = []
    by_id = {item["id"]: item for item in index["components"]}
    for component_id in components:
        manifest_path = repository / by_id[component_id]["manifest"]
        manifest = load_yaml(manifest_path)
        for doc_id in manifest.get("architecture", {}).get("profile_ids", []) or []:
            if doc_id not in ids:
                ids.append(doc_id)
    return ids


def _render_task_readme(
    task_id: str, objective: str, work_type: str, components: list[str],
    scope_boundary: str, completion_conditions: list[str], rollout_needs: str,
    architecture_doc_ids: list[str], initiative: dict[str, Any] | None,
) -> str:
    lines = [f"# {task_id}", "", f"**Work type:** {work_type}", "", "## Objective", "", objective, ""]
    if initiative:
        lines += [
            "## Initiative", "",
            f"Part of initiative `{initiative['id']}`.",
        ]
        if initiative.get("depends_on"):
            lines += ["Depends on: " + ", ".join(initiative["depends_on"])]
        lines += [""]
    lines += ["## Components", "", *(f"- `{component_id}`" for component_id in components), ""]
    if architecture_doc_ids:
        lines += ["## Referenced architecture documents", "", *(f"- `{doc_id}`" for doc_id in architecture_doc_ids), ""]
    lines += ["## Scope boundary", "", scope_boundary or "(none stated)", ""]
    lines += ["## Completion conditions", "", *(f"- {condition}" for condition in completion_conditions), ""]
    if rollout_needs:
        lines += ["## Rollout needs", "", rollout_needs, ""]
    return "\n".join(lines)


def generate_single_repository_workflow(
    repository: Path,
    repository_id: str,
    task_id: str,
    objective: str,
    work_type: str,
    components: list[str],
    scope_boundary: str,
    completion_conditions: list[str],
    rollout_needs: str = "",
    initiative: dict[str, Any] | None = None,
) -> list[Path]:
    """
    Validates components resolve against the repository's own index before writing
    anything -- no partial task.yaml/README.md pair left behind on a bad reference.
    """
    if not TASK_ID.fullmatch(task_id):
        raise ValueError("task ID is not safe for workflow discovery")
    if work_type not in WORK_TYPES:
        raise ValueError(f"work_type must be one of: {', '.join(WORK_TYPES)}")
    if not completion_conditions:
        raise ValueError("completion_conditions must be a non-empty list")
    _validate_components(repository, components)

    index = _load_index(repository)
    architecture_doc_ids = _architecture_doc_ids(repository, index, components)

    task_document: dict[str, Any] = {
        "schema_version": 1,
        "task": {
            "id": task_id, "title": objective[:80], "objective": objective,
            "repository": repository_id, "status": "ready",
        },
        "scope": {"components": components},
        "completion_conditions": completion_conditions,
    }
    if initiative:
        task_document["task"]["initiative"] = initiative

    task_dir = repository / ".esc-ai" / "workflows" / "active" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    task_path = task_dir / "task.yaml"
    readme_path = task_dir / "README.md"
    write_yaml(task_path, task_document)
    readme_path.write_text(
        _render_task_readme(
            task_id, objective, work_type, components, scope_boundary,
            completion_conditions, rollout_needs, architecture_doc_ids, initiative,
        ),
        encoding="utf-8",
    )
    return [task_path, readme_path]


def generate_multi_repository_workflow(
    registry_path: Path,
    initiative_id: str,
    objective: str,
    work_type: str,
    tasks: dict[str, dict[str, Any]],
) -> dict[str, list[Path]]:
    """
    tasks maps repository_id -> {"task_id", "components", "scope_boundary",
    "completion_conditions", "rollout_needs"?, "depends_on"?: ["repo/task_id", ...]}.

    Validates every referenced repository ID resolves, every task_id is safe, and
    every depends_on entry references another declared repository/task_id in this
    same initiative -- all before writing a single file to any repository. A
    multi-repository initiative must not partially land across some repos and fail
    on others without the human knowing which succeeded.
    """
    if work_type not in WORK_TYPES:
        raise ValueError(f"work_type must be one of: {', '.join(WORK_TYPES)}")

    errors: list[str] = []
    resolved: dict[str, Path] = {}
    declared = {f"{repository_id}/{task['task_id']}" for repository_id, task in tasks.items()}
    for repository_id, task in tasks.items():
        try:
            resolved[repository_id] = resolve_route(registry_path, "repositories", repository_id)
        except (KeyError, FileNotFoundError) as exc:
            errors.append(str(exc))
            continue
        if not TASK_ID.fullmatch(task["task_id"]):
            errors.append(f"task ID for `{repository_id}` is not safe for workflow discovery: {task['task_id']}")
        for dependency in task.get("depends_on", []) or []:
            if dependency not in declared:
                errors.append(f"`{repository_id}/{task['task_id']}` depends_on `{dependency}`, which is not part of this initiative")
        try:
            _validate_components(resolved[repository_id], task["components"])
        except ValueError as exc:
            errors.append(f"`{repository_id}`: {exc}")
        if not task.get("completion_conditions"):
            errors.append(f"`{repository_id}`: completion_conditions must be a non-empty list")
    if errors:
        raise ValueError("; ".join(errors))

    written: dict[str, list[Path]] = {}
    for repository_id, task in tasks.items():
        initiative = {"id": initiative_id}
        if task.get("depends_on"):
            initiative["depends_on"] = task["depends_on"]
        written[repository_id] = generate_single_repository_workflow(
            resolved[repository_id], repository_id, task["task_id"], objective, work_type,
            task["components"], task.get("scope_boundary", ""), task["completion_conditions"],
            task.get("rollout_needs", ""), initiative,
        )
    return written
