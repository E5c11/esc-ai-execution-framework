from __future__ import annotations

from typing import Any


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
