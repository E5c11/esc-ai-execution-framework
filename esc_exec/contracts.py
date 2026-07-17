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
    "verification-summary": "json",
    "task-context": "json",
    "verification-plan": "json",
    "architecture-report": "json",
    "dependency-graph": "json",
    "impact-analysis": "json",
    "run-metrics": "json",
    "efficiency-comparison": "json",
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
    "verification-summary": {
        "root": ("schema_version", "verification", "totals", "failures", "truncation", "full_report"),
        "verification": ("profile", "source_format", "status", "generated_at"),
        "totals": ("tests", "passed", "failed", "errors", "skipped", "duration_ms"),
        "truncation": ("failures_included", "failures_omitted", "max_failures", "max_message_chars"),
        "full_report": ("path", "media_type"),
    },
    "task-context": {
        "root": ("schema_version", "task", "routing", "scope", "bounds"),
        "task": ("id", "repository", "objective"),
        "routing": ("repository_index", "components"),
        "scope": ("paths", "references", "completion_conditions"),
        "bounds": ("max_components", "max_paths", "max_references"),
    },
    "verification-plan": {
        "root": ("schema_version", "task_id", "profiles", "strategy", "impact", "gates"),
        "strategy": ("order", "stop_on_failure"),
        "impact": ("graph", "source_components", "consumer_components"),
    },
    "architecture-report": {
        "root": ("schema_version", "component", "profile", "status", "totals", "results", "generated_at"),
        "totals": ("rules", "passed", "failed", "violations", "violations_included", "violations_omitted"),
    },
    "dependency-graph": {
        "root": ("schema_version", "generated_by", "input_digest", "repository", "nodes", "edges"),
    },
    "impact-analysis": {
        "root": ("schema_version", "repository", "graph", "source_components", "direct_consumers", "transitive_consumers", "affected_components"),
    },
    "run-metrics": {
        "root": ("schema_version", "run", "context", "execution", "tokens", "generated_at"),
        "run": ("id", "task_id", "provider", "status"),
        "context": ("bytes", "components", "paths", "references"),
        "execution": ("elapsed_ms", "tool_calls", "read_calls", "agent_messages", "rework_events"),
        "tokens": ("status",),
    },
    "efficiency-comparison": {
        "root": ("schema_version", "baseline_runs", "candidate_runs", "dimensions", "generated_at"),
    },
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
    "verification-summary": {
        "verification.source_format": {"junit-xml"},
        "verification.status": {"passed", "failed", "error"},
    },
    "architecture-report": {"status": {"passed", "failed"}},
    "run-metrics": {
        "run.status": {"succeeded", "failed", "cancelled", "interrupted"},
        "tokens.status": {"reported", "unavailable"},
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
        if kind == "checkpoint":
            progress = document.get("progress", {})
            for field in ("completed", "decisions", "remaining", "blockers", "artifacts"):
                values = progress.get(field, [])
                if not isinstance(values, list) or len(values) > 50 or not all(isinstance(value, str) and 0 < len(value) <= 1000 for value in values):
                    messages.append(prefix + f"progress.{field} must contain at most 50 non-empty bounded strings")
            status = _value_at(document, "checkpoint.status")
            if status == "blocked" and not progress.get("blockers"):
                messages.append(prefix + "blocked checkpoints require at least one blocker")
            if status == "ready-to-resume" and not progress.get("remaining"):
                messages.append(prefix + "ready-to-resume checkpoints require remaining work")
            for field in ("checkpoint.task_path",):
                value = _value_at(document, field)
                if isinstance(value, str) and Path(value).is_absolute():
                    messages.append(prefix + f"{field} must be repository-relative")
            for artifact in progress.get("artifacts", []):
                if isinstance(artifact, str) and Path(artifact).is_absolute():
                    messages.append(prefix + "progress.artifacts must be repository/run-relative")
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
        if kind == "verification-summary":
            totals = document.get("totals", {})
            numeric_fields = ("tests", "passed", "failed", "errors", "skipped", "duration_ms")
            if any(not isinstance(totals.get(field), int) or totals[field] < 0 for field in numeric_fields):
                messages.append(prefix + "totals fields must be non-negative integers")
            elif totals["passed"] + totals["failed"] + totals["errors"] + totals["skipped"] != totals["tests"]:
                messages.append(prefix + "totals result counts must add up to totals.tests")
            failures = document.get("failures")
            truncation = document.get("truncation", {})
            limit_fields = ("failures_included", "failures_omitted", "max_failures", "max_message_chars")
            valid_limits = all(isinstance(truncation.get(field), int) and truncation[field] >= 0 for field in limit_fields)
            if not valid_limits or truncation.get("max_message_chars", 0) < 1:
                messages.append(prefix + "truncation fields must be non-negative integers and max_message_chars must be positive")
            if not isinstance(failures, list):
                messages.append(prefix + "failures must be an array")
            else:
                if truncation.get("failures_included") != len(failures):
                    messages.append(prefix + "truncation.failures_included must equal the failures array length")
                if valid_limits and len(failures) > truncation["max_failures"]:
                    messages.append(prefix + "failures array exceeds truncation.max_failures")
                for index, failure in enumerate(failures):
                    if not isinstance(failure, dict) or any(field not in failure for field in ("test", "kind", "message")):
                        messages.append(prefix + f"failures[{index}] must contain test, kind, and message")
                        continue
                    if failure["kind"] not in {"failure", "error"}:
                        messages.append(prefix + f"failures[{index}].kind must be failure or error")
                    if valid_limits and (not isinstance(failure["message"], str) or len(failure["message"]) > truncation["max_message_chars"]):
                        messages.append(prefix + f"failures[{index}].message exceeds max_message_chars")
            if all(isinstance(totals.get(field), int) for field in ("failed", "errors")):
                expected_status = "error" if totals["errors"] else "failed" if totals["failed"] else "passed"
                if _value_at(document, "verification.status") != expected_status:
                    messages.append(prefix + f"verification.status must be {expected_status} for these totals")
            report_path = _value_at(document, "full_report.path")
            if isinstance(report_path, str) and Path(report_path).is_absolute():
                messages.append(prefix + "full_report.path must be workspace-relative")
        if kind == "task-context":
            routing_components = _value_at(document, "routing.components")
            bounds = document.get("bounds", {})
            for field in ("max_components", "max_paths", "max_references"):
                if not isinstance(bounds.get(field), int) or bounds[field] < (1 if field == "max_components" else 0):
                    messages.append(prefix + f"bounds.{field} has an invalid limit")
            if not isinstance(routing_components, list) or not routing_components:
                messages.append(prefix + "routing.components must be a non-empty array")
            elif isinstance(bounds.get("max_components"), int) and len(routing_components) > bounds["max_components"]:
                messages.append(prefix + "routing.components exceeds bounds.max_components")
            for field in ("paths", "references"):
                values = _value_at(document, f"scope.{field}")
                if not isinstance(values, list) or not all(isinstance(value, str) and not Path(value).is_absolute() for value in values):
                    messages.append(prefix + f"scope.{field} must contain workspace-relative strings")
                elif isinstance(bounds.get(f"max_{field}"), int) and len(values) > bounds[f"max_{field}"]:
                    messages.append(prefix + f"scope.{field} exceeds bounds.max_{field}")
        if kind == "verification-plan":
            expected_order = ["focused", "component", "impact", "final"]
            if _value_at(document, "strategy.order") != expected_order or _value_at(document, "strategy.stop_on_failure") is not True:
                messages.append(prefix + "strategy must use focused, component, impact, final order and stop on failure")
            gates = document.get("gates")
            if not isinstance(gates, list) or [gate.get("id") for gate in gates if isinstance(gate, dict)] != expected_order:
                messages.append(prefix + "gates must contain the four ordered verification stages")
            else:
                for gate in gates:
                    checks = gate.get("checks")
                    if gate.get("status") not in {"ready", "input-required", "not-applicable"} or not isinstance(checks, list):
                        messages.append(prefix + f"gate {gate.get('id')} has invalid status or checks")
                        continue
                    if gate["status"] == "not-applicable" and checks:
                        messages.append(prefix + f"gate {gate['id']} cannot be not-applicable with checks")
                    for check in checks:
                        if not isinstance(check, dict) or not check.get("id") or not isinstance(check.get("command"), list) or not check["command"]:
                            messages.append(prefix + f"gate {gate['id']} contains an invalid check")
        if kind == "architecture-report":
            totals = document.get("totals", {})
            fields = ("rules", "passed", "failed", "violations", "violations_included", "violations_omitted")
            if any(not isinstance(totals.get(field), int) or totals[field] < 0 for field in fields):
                messages.append(prefix + "architecture totals must be non-negative integers")
            else:
                if totals["rules"] < 1 or totals["passed"] + totals["failed"] != totals["rules"]:
                    messages.append(prefix + "architecture rule totals are inconsistent")
                if totals["violations_included"] + totals["violations_omitted"] != totals["violations"]:
                    messages.append(prefix + "architecture violation totals are inconsistent")
                expected_status = "failed" if totals["failed"] else "passed"
                if document.get("status") != expected_status:
                    messages.append(prefix + f"architecture status must be {expected_status}")
            results = document.get("results")
            if not isinstance(results, list) or len(results) != totals.get("rules"):
                messages.append(prefix + "results length must equal totals.rules")
            elif len({result.get("rule_id") for result in results if isinstance(result, dict)}) != len(results):
                messages.append(prefix + "result rule IDs must be unique")
        if kind == "dependency-graph":
            nodes = document.get("nodes")
            edges = document.get("edges")
            if not isinstance(nodes, list) or not all(isinstance(node, dict) and node.get("id") and node.get("project") for node in nodes):
                messages.append(prefix + "dependency nodes must contain IDs and Gradle projects")
            elif len({node["id"] for node in nodes}) != len(nodes):
                messages.append(prefix + "dependency node IDs must be unique")
            if not isinstance(edges, list):
                messages.append(prefix + "dependency edges must be an array")
            elif isinstance(nodes, list):
                node_ids = {node.get("id") for node in nodes if isinstance(node, dict)}
                for edge in edges:
                    if not isinstance(edge, dict) or edge.get("consumer") not in node_ids or edge.get("dependency") not in node_ids:
                        messages.append(prefix + "dependency edges must reference declared nodes")
                        break
        if kind == "impact-analysis":
            sources = document.get("source_components")
            direct = document.get("direct_consumers")
            transitive = document.get("transitive_consumers")
            affected = document.get("affected_components")
            if not all(isinstance(values, list) and all(isinstance(value, str) for value in values) for values in (sources, direct, transitive, affected)):
                messages.append(prefix + "impact component fields must be string arrays")
            elif set(affected) != set(sources) | set(transitive) or not set(direct) <= set(transitive):
                messages.append(prefix + "impact component sets are inconsistent")
        if kind == "run-metrics":
            for section, fields in {
                "context": ("bytes", "components", "paths", "references"),
                "execution": ("elapsed_ms", "tool_calls", "read_calls", "agent_messages", "rework_events"),
            }.items():
                values = document.get(section, {})
                if any(not isinstance(values.get(field), int) or values[field] < 0 for field in fields):
                    messages.append(prefix + f"{section} metrics must be non-negative integers")
            tokens = document.get("tokens", {})
            token_fields = ("input", "output", "reasoning", "cache_read", "cache_write", "total")
            if any(field not in tokens for field in token_fields):
                messages.append(prefix + "token metric fields must be present")
            if tokens.get("status") == "reported":
                if any(not isinstance(tokens.get(field), int) or tokens[field] < 0 for field in token_fields):
                    messages.append(prefix + "reported token metrics must be non-negative integers")
                elif tokens["total"] != tokens["input"] + tokens["output"] + tokens["reasoning"]:
                    messages.append(prefix + "tokens.total must equal input + output + reasoning")
            elif tokens.get("status") == "unavailable" and any(tokens.get(field) is not None for field in token_fields):
                messages.append(prefix + "unavailable token metrics must be null")
        if kind == "efficiency-comparison":
            for field in ("baseline_runs", "candidate_runs"):
                values = document.get(field)
                if not isinstance(values, list) or not values or not all(isinstance(value, str) for value in values):
                    messages.append(prefix + f"{field} must be a non-empty string array")
            dimensions = document.get("dimensions")
            if not isinstance(dimensions, dict) or not dimensions:
                messages.append(prefix + "dimensions must be a non-empty object")
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
