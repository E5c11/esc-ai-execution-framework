from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.manifests import repository_manifest_path
from esc_exec.roadmap import project_roadmap_path
from esc_exec.workflow_bootstrap import WORKFLOW_README_PATH, parse_workflow_policy_frontmatter
from esc_exec.yaml_io import load_yaml


# The plan's composed instruction order (plan doc: "Instruction precedence"). Fixed
# and machine-readable so no interface has to invent or re-derive an ordering.
PRECEDENCE: tuple[str, ...] = (
    "safety_and_operator_policy",
    "execution_framework_core",
    "architecture_framework_core_and_profile",
    "repository_instructions_and_workflow_policy",
    "component_manifests_and_profiles",
    "active_workflow_task_specification",
)

# Owned exclusively by the architecture framework (see its INSTRUCTIONS.md's Document
# IDs table). A project-specific extension must use its own prefix instead.
RESERVED_DOCUMENT_PREFIXES: tuple[str, ...] = (
    "CORE-", "PAT-", "ARCH-", "PLAT-", "BUILD-", "QG-", "ORCH-",
)


def order_instruction_bundle(bundle: dict[str, list[str]]) -> list[dict[str, Any]]:
    """
    Arrange labeled instruction sources into the plan's fixed precedence order.

    `bundle` maps a precedence level name (see PRECEDENCE) to its list of sources
    (document IDs, file paths, or any other opaque source identifier). Levels absent
    from `bundle` or with an empty list are omitted from the result rather than
    appearing as empty entries. An unknown level name is a caller error, not a level
    to silently ignore.
    """
    unknown = sorted(set(bundle) - set(PRECEDENCE))
    if unknown:
        raise ValueError(f"unknown instruction precedence level(s): {', '.join(unknown)}")
    return [{"level": level, "sources": bundle[level]} for level in PRECEDENCE if bundle.get(level)]


def check_extension_namespace_conflict(extension_doc_ids: list[str]) -> list[str]:
    """
    Return the subset of a project-specific extension's declared document IDs that
    collide with a reserved architecture-framework prefix.

    Project-specific extensions (e.g. `ampm-backend-framework`) specialize generic
    architecture guidance only in their own declared namespace (a project-specific
    prefix like `AMPM-BE-`) — they do not define IDs under a prefix the architecture
    framework owns. Conflicts must be surfaced, not silently accepted by whichever
    document an agent happens to read last.
    """
    return [doc_id for doc_id in extension_doc_ids if doc_id.startswith(RESERVED_DOCUMENT_PREFIXES)]


def build_instruction_bundle(
    repository: Path, context: dict[str, Any], policy: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Compose the plan's precedence-ordered instruction bundle for a run, and check it
    for the one named conflict case: a project-specific workflow-policy extension
    declaring a document ID under a prefix the architecture framework owns.

    Provider-agnostic -- every adapter (OpenCode, Claude Code, ...) composes the same
    bundle from the same repository/context/policy inputs; nothing here depends on
    which agent runtime will consume it.

    The extension-conflict check is always a no-op today: .esc-ai/workflows/
    README.md's policy.extension frontmatter (see workflow_bootstrap.py) only
    declares an id/precedence note, not an enumerated list of document IDs, so
    there is nothing to check yet. This wiring is real and will start doing
    something the moment that declaration mechanism grows a document-ID list --
    it is not fabricating a source that doesn't exist to look finished.
    """
    bundle: dict[str, list[str]] = {}

    policy_id = policy.get("id")
    bundle["safety_and_operator_policy"] = [f"policy:{policy_id}"] if policy_id else []

    manifest_path = repository_manifest_path(repository)
    frameworks = {}
    if manifest_path.is_file():
        frameworks = load_yaml(manifest_path).get("frameworks") or {}
    execution_framework_id = "esc-ai-execution-framework"
    bundle["execution_framework_core"] = [
        f"{execution_framework_id}:{frameworks[execution_framework_id]}"
        if execution_framework_id in frameworks else execution_framework_id
    ]

    architecture_docs: list[str] = []
    for component in context["routing"]["components"]:
        for document in component.get("architecture", {}).get("documents", []):
            if document["id"] not in architecture_docs:
                architecture_docs.append(document["id"])
    bundle["architecture_framework_core_and_profile"] = architecture_docs

    extension_doc_ids: list[str] = []
    workflow_sources: list[str] = []
    workflow_readme_path = repository / WORKFLOW_README_PATH
    if workflow_readme_path.is_file():
        workflow_sources.append(str(WORKFLOW_README_PATH))
        frontmatter = parse_workflow_policy_frontmatter(workflow_readme_path.read_text(encoding="utf-8"))
        extension = frontmatter.get("policy", {}).get("extension")
        if isinstance(extension, dict) and extension.get("id"):
            workflow_sources.append(f"extension:{extension['id']}")
    roadmap_path = project_roadmap_path(repository)
    if roadmap_path.is_file():
        # Provenance only, matching every other source in this bucket -- see
        # plan/done/project-vision-and-direction.md design 2 for the actual
        # functional fix (each adapter's real prompt), which this is not.
        workflow_sources.append(str(roadmap_path.relative_to(repository)))
    bundle["repository_instructions_and_workflow_policy"] = workflow_sources

    bundle["component_manifests_and_profiles"] = [
        component["manifest"] for component in context["routing"]["components"]
    ]
    bundle["active_workflow_task_specification"] = [context["task"]["id"]]

    return order_instruction_bundle(bundle), check_extension_namespace_conflict(extension_doc_ids)
