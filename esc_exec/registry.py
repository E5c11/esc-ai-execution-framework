from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from esc_exec.model import ManifestState, ValidationResult
from esc_exec.yaml_io import load_yaml, write_yaml


# Framework IDs that were renamed after routes/manifests may already reference the old
# name. Kept so a stale reference gets an exact repair action instead of either a
# generic "not registered" error or a silently tolerated second identity.
RENAMED_FRAMEWORK_IDS: dict[str, str] = {
    "esc-ai-framework": "esc-ai-architecture-framework",
}


def default_registry_path() -> Path:
    override = os.environ.get("ESC_AI_REGISTRY")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "esc-ai" / "repositories.yaml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "esc-ai" / "repositories.yaml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "esc-ai" / "repositories.yaml"


def empty_registry() -> dict[str, Any]:
    return {"schema_version": 1, "repositories": {}, "frameworks": {}}


def read_registry(path: Path) -> dict[str, Any]:
    return load_yaml(path) if path.exists() else empty_registry()


def add_route(path: Path, category: str, route_id: str, target: Path) -> None:
    data = read_registry(path)
    data.setdefault("schema_version", 1)
    routes = data.setdefault(category, {})
    routes[route_id] = {"path": str(target.expanduser().resolve())}
    write_yaml(path, data)


def resolve_route(path: Path, category: str, route_id: str) -> Path:
    data = read_registry(path)
    route = data.get(category, {}).get(route_id)
    if not route:
        if category == "frameworks" and route_id in RENAMED_FRAMEWORK_IDS:
            renamed = RENAMED_FRAMEWORK_IDS[route_id]
            if renamed in data.get(category, {}):
                raise KeyError(
                    f"Framework `{route_id}` was renamed to `{renamed}`. "
                    f"Update the reference (manifest `frameworks` field or route id) to `{renamed}`."
                )
        kind = {"repositories": "repository", "frameworks": "framework"}[category]
        raise KeyError(
            f"{kind.capitalize()} route `{route_id}` is not registered. "
            f"Run: esc-exec route add {kind} {route_id} /path/to/{route_id}"
        )
    target = Path(route.get("path", "")).expanduser()
    if not target.is_dir():
        raise FileNotFoundError(
            f"Route `{route_id}` points to missing directory: {target}. "
            "Update or remove the stale route."
        )
    return target.resolve()


def validate_registry(path: Path) -> ValidationResult:
    if not path.exists():
        return ValidationResult(
            ManifestState.INCOMPLETE,
            str(path),
            ["Route registry does not exist; add the first repository or framework route."],
        )
    try:
        data = load_yaml(path)
    except (OSError, ValueError) as exc:
        return ValidationResult(ManifestState.INVALID, str(path), [str(exc)])
    messages: list[str] = []
    if data.get("schema_version") != 1:
        messages.append("schema_version must be 1")
    unknown = sorted(set(data) - {"schema_version", "repositories", "frameworks"})
    for key in unknown:
        messages.append(f"unknown top-level field: {key}")
    stale = False
    for category in ("repositories", "frameworks"):
        routes = data.get(category, {})
        if not isinstance(routes, dict):
            messages.append(f"{category} must be a mapping")
            continue
        for route_id, route in routes.items():
            if not isinstance(route, dict) or not isinstance(route.get("path"), str):
                messages.append(f"{category}.{route_id}.path must be a string")
            elif not Path(route["path"]).expanduser().is_dir():
                stale = True
                messages.append(f"{category}.{route_id} points to missing directory: {route['path']}")
            if category == "frameworks" and route_id in RENAMED_FRAMEWORK_IDS:
                stale = True
                messages.append(
                    f"frameworks.{route_id} uses a renamed framework ID; "
                    f"re-register it as `{RENAMED_FRAMEWORK_IDS[route_id]}`."
                )
    if any("must" in message or message.startswith("unknown") for message in messages):
        return ValidationResult(ManifestState.INVALID, str(path), messages)
    if stale:
        return ValidationResult(ManifestState.STALE, str(path), messages)
    return ValidationResult(ManifestState.VALID, str(path), messages)
