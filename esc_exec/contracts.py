from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from esc_exec.json_io import load_json
from esc_exec.model import ManifestState, ValidationResult
from esc_exec.yaml_io import load_yaml


CONTRACT_FORMATS = {
    "task": "yaml",
    "workspace": "yaml",
    "adapter": "yaml",
    "policy": "yaml",
    "checkpoint": "yaml",
    "run": "json",
    "artifact": "json",
    "event": "jsonl",
}

REQUIRED: dict[str, dict[str, tuple[str, ...]]] = {
    "task": {"root": ("schema_version", "task", "scope", "completion_conditions"), "task": ("id", "title", "objective", "repository", "status")},
    "workspace": {"root": ("schema_version", "workspace"), "workspace": ("id", "kind", "repository", "isolation")},
    "adapter": {"root": ("schema_version", "adapter"), "adapter": ("id", "kind", "provider", "capabilities")},
    "policy": {"root": ("schema_version", "policy", "permissions"), "policy": ("id", "description")},
    "checkpoint": {"root": ("schema_version", "checkpoint", "progress"), "checkpoint": ("id", "task_id", "status", "objective"), "progress": ("completed", "decisions", "remaining", "blockers")},
    "run": {"root": ("schema_version", "run", "bindings", "events", "artifacts"), "run": ("id", "task_id", "status", "created_at"), "bindings": ("adapter", "workspace", "policy")},
    "artifact": {"root": ("schema_version", "artifact"), "artifact": ("id", "run_id", "kind", "path", "retention", "created_at")},
    "event": {"root": ("schema_version", "event"), "event": ("id", "run_id", "sequence", "timestamp", "type", "actor", "payload")},
}

ENUMS: dict[str, dict[str, set[str]]] = {
    "task": {"task.status": {"draft", "ready", "active", "blocked", "complete", "cancelled"}},
    "workspace": {
        "workspace.kind": {"local", "worktree", "container", "remote"},
        "workspace.isolation": {"none", "process", "filesystem", "container", "remote"},
    },
    "adapter": {"adapter.kind": {"agent-runtime", "build-system", "test-framework", "reporter", "source-control"}},
    "checkpoint": {"checkpoint.status": {"active", "blocked", "ready-to-resume"}},
    "run": {"run.status": {"queued", "running", "waiting-approval", "succeeded", "failed", "cancelled", "interrupted"}},
    "artifact": {
        "artifact.kind": {"log", "report", "diff", "test-results", "coverage", "checkpoint", "other"},
        "artifact.retention": {"committed", "transient"},
    },
    "event": {
        "event.type": {
            "run.started", "run.status-changed", "message.created", "tool.requested",
            "tool.completed", "approval.requested", "approval.resolved", "artifact.created",
            "checkpoint.created", "run.completed", "run.failed",
        },
        "event.actor": {"user", "agent", "orchestrator", "adapter", "tool", "system"},
    },
}


def _read_contract(kind: str, path: Path) -> list[dict[str, Any]]:
    format_name = CONTRACT_FORMATS[kind]
    if format_name == "yaml":
        return [load_yaml(path)]
    if format_name == "json":
        return [load_json(path)]
    documents: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        value["__line__"] = line_number
        documents.append(value)
    if not documents:
        raise ValueError(f"{path} contains no JSONL events")
    return documents


def _value_at(data: dict[str, Any], dotted: str) -> Any:
    value: Any = data
    for part in dotted.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def validate_contract(kind: str, path: Path) -> ValidationResult:
    if kind not in CONTRACT_FORMATS:
        return ValidationResult(ManifestState.INVALID, str(path), [f"Unknown contract kind: {kind}"])
    if not path.exists():
        return ValidationResult(ManifestState.INCOMPLETE, str(path), ["Contract file does not exist"])
    try:
        documents = _read_contract(kind, path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ValidationResult(ManifestState.INVALID, str(path), [str(exc)])
    messages: list[str] = []
    last_sequence = -1
    for position, document in enumerate(documents, start=1):
        line = document.pop("__line__", None)
        prefix = f"line {line}: " if line else ""
        if document.get("schema_version") != 1:
            messages.append(prefix + "schema_version must be 1")
        requirements = REQUIRED[kind]
        for section, fields in requirements.items():
            container = document if section == "root" else document.get(section)
            if not isinstance(container, dict):
                messages.append(prefix + f"{section} must be a mapping/object")
                continue
            for field in fields:
                if field not in container or container[field] in (None, ""):
                    location = field if section == "root" else f"{section}.{field}"
                    messages.append(prefix + f"{location} is required")
        for dotted, allowed in ENUMS.get(kind, {}).items():
            value = _value_at(document, dotted)
            if value is not None and value not in allowed:
                messages.append(prefix + f"{dotted} must be one of: {', '.join(sorted(allowed))}")
        if kind == "event":
            sequence = _value_at(document, "event.sequence")
            if not isinstance(sequence, int) or sequence < 0:
                messages.append(prefix + "event.sequence must be a non-negative integer")
            elif sequence <= last_sequence:
                messages.append(prefix + "event.sequence must increase monotonically")
            else:
                last_sequence = sequence
        if kind == "task":
            components = _value_at(document, "scope.components")
            if not isinstance(components, list) or not all(isinstance(item, str) and item for item in components):
                messages.append(prefix + "scope.components must be a non-empty string list")
            conditions = document.get("completion_conditions")
            if not isinstance(conditions, list) or not conditions:
                messages.append(prefix + "completion_conditions must be a non-empty list")
        if kind == "adapter":
            capabilities = _value_at(document, "adapter.capabilities")
            if not isinstance(capabilities, list) or not capabilities:
                messages.append(prefix + "adapter.capabilities must be a non-empty list")
        if kind == "policy":
            permissions = document.get("permissions")
            if isinstance(permissions, dict):
                for permission, value in permissions.items():
                    if value not in {"allow", "ask", "deny"}:
                        messages.append(prefix + f"permissions.{permission} must be allow, ask, or deny")
        if kind == "artifact":
            artifact_path = _value_at(document, "artifact.path")
            if isinstance(artifact_path, str) and Path(artifact_path).is_absolute():
                messages.append(prefix + "artifact.path must be repository/workspace-relative")
    state = ManifestState.INVALID if messages else ManifestState.VALID
    return ValidationResult(state, str(path), messages)


def validate_contract_set(directory: Path) -> list[ValidationResult]:
    files = {
        "task": directory / "task.yaml",
        "workspace": directory / "workspace.yaml",
        "adapter": directory / "adapter.yaml",
        "policy": directory / "policy.yaml",
        "run": directory / "run.json",
        "event": directory / "events.jsonl",
        "artifact": directory / "artifact.json",
        "checkpoint": directory / "checkpoint.yaml",
    }
    results = [validate_contract(kind, path) for kind, path in files.items()]
    if any(result.state != ManifestState.VALID for result in results):
        return results
    task = load_yaml(files["task"])["task"]
    workspace = load_yaml(files["workspace"])["workspace"]
    adapter = load_yaml(files["adapter"])["adapter"]
    policy = load_yaml(files["policy"])["policy"]
    run_document = load_json(files["run"])
    run = run_document["run"]
    artifact = load_json(files["artifact"])["artifact"]
    checkpoint = load_yaml(files["checkpoint"])["checkpoint"]
    event_documents = _read_contract("event", files["event"])
    messages: list[str] = []
    if run["task_id"] != task["id"]:
        messages.append("run.task_id does not reference task.id")
    expected_bindings = {"workspace": workspace["id"], "adapter": adapter["id"], "policy": policy["id"]}
    for field, expected in expected_bindings.items():
        if run_document["bindings"].get(field) != expected:
            messages.append(f"run.bindings.{field} does not reference the declared {field}.id")
    if artifact["run_id"] != run["id"]:
        messages.append("artifact.run_id does not reference run.id")
    if checkpoint["task_id"] != task["id"] or checkpoint.get("run_id") != run["id"]:
        messages.append("checkpoint task_id/run_id does not reference the declared task and run")
    for event_document in event_documents:
        if event_document["event"]["run_id"] != run["id"]:
            messages.append("an event.run_id does not reference run.id")
            break
    results.append(ValidationResult(
        ManifestState.INVALID if messages else ManifestState.VALID,
        str(directory),
        messages,
    ))
    return results
