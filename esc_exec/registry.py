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

# The catalog's previous filename. system.yaml replaces it; migrate_legacy_registry
# is the explicit, non-interactive path from one to the other -- never automatic.
LEGACY_REGISTRY_FILENAME = "repositories.yaml"


def default_registry_path() -> Path:
    override = os.environ.get("ESC_AI_REGISTRY")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / "esc-ai" / "system.yaml"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "esc-ai" / "system.yaml"
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "esc-ai" / "system.yaml"


def empty_registry() -> dict[str, Any]:
    return {"schema_version": 1, "repositories": {}, "frameworks": {}, "ecosystems": {}}


def read_registry(path: Path) -> dict[str, Any]:
    return load_yaml(path) if path.exists() else empty_registry()


def migrate_legacy_registry(new_path: Path) -> Path | None:
    """
    Migrate a legacy repositories.yaml sibling into new_path (system.yaml), explicitly
    and only when asked -- never as a side effect of reading or resolving a route.

    Returns new_path on a successful migration, or None if there was nothing to do
    (new_path already exists, or no legacy file was found beside it).
    """
    if new_path.exists():
        return None
    legacy_path = new_path.parent / LEGACY_REGISTRY_FILENAME
    if not legacy_path.exists():
        return None
    data = load_yaml(legacy_path)
    data.setdefault("schema_version", 1)
    write_yaml(new_path, data)
    return new_path


def add_route(path: Path, category: str, route_id: str, target: Path) -> None:
    data = read_registry(path)
    data.setdefault("schema_version", 1)
    routes = data.setdefault(category, {})
    routes[route_id] = {"path": str(target.expanduser().resolve())}
    write_yaml(path, data)


def add_ecosystem(path: Path, name: str, repository_ids: list[str]) -> None:
    data = read_registry(path)
    data.setdefault("schema_version", 1)
    ecosystems = data.setdefault("ecosystems", {})
    ecosystems[name] = {"repositories": list(repository_ids)}
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
    known_top_level = {
        "schema_version", "repositories", "frameworks", "ecosystems",
        "orchestrator", "ui", "credentials",
    }
    unknown = sorted(set(data) - known_top_level)
    for key in unknown:
        messages.append(f"unknown top-level field: {key}")
    stale = False
    kind_by_category = {"repositories": "repository", "frameworks": "framework"}
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
                messages.append(
                    f"{category}.{route_id} points to missing directory: {route['path']}. "
                    f"Run: esc-exec route add {kind_by_category[category]} {route_id} /new/path"
                )
            if category == "frameworks" and route_id in RENAMED_FRAMEWORK_IDS:
                stale = True
                messages.append(
                    f"frameworks.{route_id} uses a renamed framework ID; "
                    f"re-register it as `{RENAMED_FRAMEWORK_IDS[route_id]}`. "
                    f"Run: esc-exec route add framework {RENAMED_FRAMEWORK_IDS[route_id]} /path/to/it"
                )
    orchestrator = data.get("orchestrator")
    if orchestrator is not None:
        if not isinstance(orchestrator, dict) or not set(orchestrator) <= {"endpoint"}:
            messages.append("orchestrator must be a mapping with only an optional 'endpoint' field")
        elif "endpoint" in orchestrator and (
            not isinstance(orchestrator["endpoint"], str) or not orchestrator["endpoint"].strip()
        ):
            messages.append("orchestrator.endpoint must be a non-empty string")
    ui = data.get("ui")
    if ui is not None and not isinstance(ui, dict):
        messages.append("ui must be a mapping")
    credentials = data.get("credentials")
    if credentials is not None:
        # This is a pointer to which credential provider is configured (e.g. "env", a
        # secrets-manager name) -- never an actual secret value. Real secrets belong in
        # the environment or the named provider, not in this file.
        if not isinstance(credentials, dict) or not set(credentials) <= {"provider"}:
            messages.append("credentials must be a mapping with only an optional 'provider' field")
        elif "provider" in credentials and (
            not isinstance(credentials["provider"], str) or not credentials["provider"].strip()
        ):
            messages.append("credentials.provider must be a non-empty string naming the provider")
    ecosystems = data.get("ecosystems", {})
    if not isinstance(ecosystems, dict):
        messages.append("ecosystems must be a mapping")
    else:
        registered_repositories = data.get("repositories", {})
        registered_repository_ids = set(registered_repositories) if isinstance(registered_repositories, dict) else set()
        for name, ecosystem in ecosystems.items():
            if not isinstance(ecosystem, dict) or not isinstance(ecosystem.get("repositories"), list) or not ecosystem["repositories"]:
                messages.append(f"ecosystems.{name}.repositories must be a non-empty list")
                continue
            for repository_id in ecosystem["repositories"]:
                if repository_id not in registered_repository_ids:
                    messages.append(f"ecosystems.{name} references unregistered repository: {repository_id}")
    if any(
        "must" in message
        or message.startswith("unknown")
        or "references unregistered repository" in message
        for message in messages
    ):
        return ValidationResult(ManifestState.INVALID, str(path), messages)
    if stale:
        return ValidationResult(ManifestState.STALE, str(path), messages)
    return ValidationResult(ManifestState.VALID, str(path), messages)
