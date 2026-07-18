from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


EXECUTION_FRAMEWORK_ID = "esc-ai-execution-framework"
ARCHITECTURE_FRAMEWORK_ID = "esc-ai-architecture-framework"

INSTRUCTIONS_PATH = Path(".esc-ai/INSTRUCTIONS.md")
WORKFLOW_README_PATH = Path(".esc-ai/workflows/README.md")
WORKFLOW_ACTIVE_README_PATH = Path(".esc-ai/workflows/active/README.md")
WORKFLOW_ARCHIVE_README_PATH = Path(".esc-ai/workflows/archive/README.md")

_FRONTMATTER = re.compile(r"^---\s*\n(.*?\n)---\s*\n", re.DOTALL)


def render_instructions_md(repository_id: str) -> str:
    return (
        f"# {repository_id} — Instructions\n\n"
        f"This repository consumes `{EXECUTION_FRAMEWORK_ID}` and "
        f"`{ARCHITECTURE_FRAMEWORK_ID}`. Portable execution contracts (manifests, "
        "indexes, verification, checkpoints) are owned by the execution framework; "
        "engineering principles, patterns, and architecture styles are owned by the "
        "architecture framework. Do not duplicate either framework's content here — "
        "this file is a pointer, not a copy.\n\n"
        "See [`workflows/README.md`](workflows/README.md) (both live under `.esc-ai/`) "
        "for this repository's own workflow policy, project-specific framework "
        "extension (if any), and final gates.\n"
    )


def render_workflow_readme(repository_id: str) -> str:
    return (
        "---\n"
        "schema_version: 1\n"
        "policy: {}\n"
        "---\n\n"
        f"# Workflows — {repository_id} Policy\n\n"
        "This directory holds this repository's own workflow tracking: active and "
        f"archived task work. It does not duplicate `{EXECUTION_FRAMEWORK_ID}`'s or "
        f"`{ARCHITECTURE_FRAMEWORK_ID}`'s instructions — see [`../INSTRUCTIONS.md`]"
        "(../INSTRUCTIONS.md) for those, and the architecture framework's Gap "
        "Protocol (in its `INSTRUCTIONS.md`) for what to do when framework coverage "
        "is missing.\n\n"
        "**This is a generated starting skeleton, not a finished policy.** Fill in "
        "before treating this repository as fully onboarded:\n\n"
        "- Project-specific framework extension: does this repository have one (e.g. "
        "`<project>-backend-framework`)? If so, declare it under `policy.extension` "
        "in the frontmatter above (`id`, `precedence`) and describe its precedence "
        "over the generic architecture framework here.\n"
        "- Workflow naming/location rules specific to this repository, if any beyond "
        "the default `.esc-ai/workflows/active|archive/` convention.\n"
        "- Where active business roadmaps live, if separate from "
        "`.esc-ai/workflows/active/`.\n"
        "- Repository-specific final gates and commit conventions — list them under "
        "`policy.final_gates`/`policy.commit_conventions` in the frontmatter above.\n"
        "- Deployment or environment constraints that don't belong in a generic "
        "framework.\n"
        "- Any other exception specific to this repository that cannot be "
        "generalized into either framework.\n"
    )


def render_active_readme(repository_id: str) -> str:
    return (
        f"# Active Workflows — {repository_id}\n\n"
        "In-progress task workflows for this repository. See "
        "[`../README.md`](../README.md) for this repository's workflow policy.\n"
    )


def render_archive_readme(repository_id: str) -> str:
    return (
        f"# Archived Workflows — {repository_id}\n\n"
        "Completed task workflows for this repository, retained for history.\n"
    )


def parse_workflow_policy_frontmatter(text: str) -> dict[str, Any]:
    match = _FRONTMATTER.match(text)
    if not match:
        raise ValueError("no frontmatter block found")
    data = yaml.safe_load(match.group(1))
    return data if isinstance(data, dict) else {}


def validate_workflow_policy(frontmatter: dict[str, Any]) -> list[str]:
    """
    Manual validation against schemas/workflow-policy.schema.yaml, matching this
    repo's established convention (manifests.py, framework_descriptor.py) of hand-
    checked fields rather than a jsonschema dependency.
    """
    messages: list[str] = []
    if frontmatter.get("schema_version") != 1:
        messages.append("schema_version must be 1")
    unknown = sorted(set(frontmatter) - {"schema_version", "policy"})
    for key in unknown:
        messages.append(f"unknown top-level field: {key}")
    policy = frontmatter.get("policy")
    if not isinstance(policy, dict):
        messages.append("policy must be a mapping")
        return messages
    unknown_policy = sorted(set(policy) - {"extension", "final_gates", "commit_conventions"})
    for key in unknown_policy:
        messages.append(f"unknown policy field: {key}")
    extension = policy.get("extension")
    if extension is not None:
        if not isinstance(extension, dict):
            messages.append("policy.extension must be a mapping")
        else:
            unknown_extension = sorted(set(extension) - {"id", "precedence"})
            for key in unknown_extension:
                messages.append(f"unknown policy.extension field: {key}")
            for field in ("id", "precedence"):
                if field in extension and (not isinstance(extension[field], str) or not extension[field].strip()):
                    messages.append(f"policy.extension.{field} must be a non-empty string")
    final_gates = policy.get("final_gates")
    if final_gates is not None:
        if not isinstance(final_gates, list) or not all(isinstance(item, str) and item.strip() for item in final_gates):
            messages.append("policy.final_gates must be a list of non-empty strings")
    commit_conventions = policy.get("commit_conventions")
    if commit_conventions is not None and (not isinstance(commit_conventions, str) or not commit_conventions.strip()):
        messages.append("policy.commit_conventions must be a non-empty string")
    return messages


def check_thin_pointer(path: Path) -> list[str]:
    """
    Advisory only, run against a file this function itself just generated -- never
    against pre-existing content, which isn't reliably checkable this way and is out
    of scope for automated inspection. Flags a generated pointer that grew past what
    a pointer should be, or that looks like it embedded a real framework document's
    own frontmatter (an `id:` field, which generated pointers never contain).
    """
    text = path.read_text(encoding="utf-8")
    warnings: list[str] = []
    line_count = len(text.splitlines())
    if line_count > 80:
        warnings.append(
            f"{path} is unusually long for a thin pointer ({line_count} lines) — "
            "check it hasn't grown into duplicated framework content."
        )
    if re.search(r"^id:\s*[A-Z]+-", text, re.MULTILINE):
        warnings.append(
            f"{path} contains what looks like a framework document's own frontmatter "
            "(an `id:` field) — check for accidental duplication."
        )
    return warnings


def bootstrap_workflow_inheritance(root: Path, repository_manifest: dict[str, Any]) -> dict[str, Any]:
    """
    Idempotent: creates each of the four inheritance files only if missing. A file
    that already exists is left completely untouched and reported as `existing` --
    it is a signal for a human, never silently regenerated (see the plan's "Who this
    is for" scope boundary and the "never overwrite a mature workflow package" non-goal).
    """
    repository_id = repository_manifest.get("repository", {}).get("id", root.name)
    renderers = {
        INSTRUCTIONS_PATH: render_instructions_md(repository_id),
        WORKFLOW_README_PATH: render_workflow_readme(repository_id),
        WORKFLOW_ACTIVE_README_PATH: render_active_readme(repository_id),
        WORKFLOW_ARCHIVE_README_PATH: render_archive_readme(repository_id),
    }
    created: list[str] = []
    existing: list[str] = []
    advisory_warnings: list[str] = []
    for relative, content in renderers.items():
        path = root / relative
        if path.exists():
            existing.append(str(relative))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        created.append(str(relative))
        advisory_warnings.extend(check_thin_pointer(path))
    return {"created": created, "existing": existing, "advisory_warnings": advisory_warnings}
