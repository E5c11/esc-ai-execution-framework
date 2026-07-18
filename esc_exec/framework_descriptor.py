from __future__ import annotations

from pathlib import Path
from typing import Any

from esc_exec.model import ManifestState, ValidationResult
from esc_exec.registry import resolve_route
from esc_exec.yaml_io import load_yaml


FRAMEWORK_DESCRIPTOR = "esc-framework.yaml"


def load_framework_descriptor(framework_root: Path) -> dict[str, Any]:
    return load_yaml(framework_root / FRAMEWORK_DESCRIPTOR)


def check_framework_compatibility(repository: dict[str, Any], registry_path: Path) -> ValidationResult:
    declared = repository.get("frameworks")
    if not isinstance(declared, dict) or not declared:
        return ValidationResult(ManifestState.VALID, "frameworks", [])
    messages: list[str] = []
    stale = False
    for framework_id, major_raw in declared.items():
        if not isinstance(major_raw, str) or not major_raw.strip().isdigit():
            messages.append(
                f"frameworks.{framework_id} must declare a positive integer major version, got: {major_raw!r}"
            )
            continue
        declared_major = int(major_raw)
        try:
            framework_root = resolve_route(registry_path, "frameworks", framework_id)
        except (KeyError, FileNotFoundError) as exc:
            messages.append(f"frameworks.{framework_id}: {exc}")
            continue
        try:
            descriptor = load_framework_descriptor(framework_root)
        except (OSError, ValueError) as exc:
            messages.append(
                f"frameworks.{framework_id} could not read {FRAMEWORK_DESCRIPTOR} at {framework_root}: {exc}"
            )
            continue
        if descriptor.get("schema_version") != 1:
            messages.append(f"frameworks.{framework_id}: {FRAMEWORK_DESCRIPTOR} schema_version must be 1")
            continue
        actual_major = descriptor.get("framework", {}).get("major_version")
        if not isinstance(actual_major, int):
            messages.append(f"frameworks.{framework_id}: {FRAMEWORK_DESCRIPTOR} is missing framework.major_version")
            continue
        if actual_major != declared_major:
            stale = True
            messages.append(
                f"frameworks.{framework_id} declares major version {declared_major}, "
                f"but the checked-out framework is major version {actual_major}."
            )
    if any("must declare" in message or "is missing" in message or "could not read" in message for message in messages):
        return ValidationResult(ManifestState.INVALID, "frameworks", messages)
    if messages and any("is not registered" in message or "points to missing directory" in message or "was renamed" in message for message in messages):
        return ValidationResult(ManifestState.INVALID, "frameworks", messages)
    if stale:
        return ValidationResult(ManifestState.STALE, "frameworks", messages)
    return ValidationResult(ManifestState.VALID, "frameworks", messages)
