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


# The providers currently offered. Extensible later (see native-cli-provider-
# adapters.md's community-adapter goal) -- not a hard ceiling, just today's known
# set, so an unrecognized name fails loudly instead of being silently accepted and
# then failing later at task-run time.
#
# gemini deliberately excluded as of 2026-07-19 -- see
# plan/reintroduce-gemini-provider.md. Not removed because nothing works for it:
# Antigravity's subscription billing is confirmed real, but three live attempts all
# failed to reliably execute a given headless task. Removed for picker simplicity
# (nothing offered doesn't work), which also means the separately-working, unrelated
# OpenCode API-key route for gemini isn't offered right now either -- a deliberate
# trade, not a technical necessity; see that doc before re-adding either route.
KNOWN_PROVIDERS: tuple[str, ...] = ("claude", "openai")

# Claude (ClaudeCodeAdapter) and OpenAI (CodexAdapter, via `codex exec`) both have a
# verified first-party subscription-route adapter -- see native-cli-provider-
# adapters.md.
SUBSCRIPTION_CAPABLE_PROVIDERS: tuple[str, ...] = ("claude", "openai")

PROVIDER_ROUTES: tuple[str, ...] = ("subscription", "api-key")


def set_provider(path: Path, provider_id: str, route: str) -> None:
    """
    Record a provider connection and make it the active one -- lazy, per-provider, one
    at a time (see native-cli-provider-adapters.md open question 5: asked once, per
    provider, not a global upfront wizard). There is no per-task provider override yet;
    `active` is the whole model until that's designed.
    """
    if provider_id not in KNOWN_PROVIDERS:
        raise ValueError(f"unknown provider `{provider_id}`; known providers: {', '.join(KNOWN_PROVIDERS)}")
    if route not in PROVIDER_ROUTES:
        raise ValueError(f"route must be one of {PROVIDER_ROUTES}")
    if route == "subscription" and provider_id not in SUBSCRIPTION_CAPABLE_PROVIDERS:
        raise ValueError(f"`{provider_id}` has no subscription-route adapter yet; use route=api-key")
    data = read_registry(path)
    data.setdefault("schema_version", 1)
    providers = data.setdefault("providers", {})
    providers[provider_id] = {"route": route}
    providers["active"] = provider_id
    write_yaml(path, data)


def active_provider(path: Path) -> dict[str, Any] | None:
    """None means no provider has been connected yet -- callers must not invent a
    default; see escape_ai_cli.py's lazy first-use prompt."""
    providers = read_registry(path).get("providers", {})
    active_id = providers.get("active")
    if not active_id or active_id not in providers:
        return None
    return {"id": active_id, **providers[active_id]}


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
        "orchestrator", "ui", "credentials", "providers",
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
    providers = data.get("providers")
    if providers is not None:
        if not isinstance(providers, dict):
            messages.append("providers must be a mapping")
        else:
            active = providers.get("active")
            for provider_id, config in providers.items():
                if provider_id == "active":
                    continue
                if provider_id not in KNOWN_PROVIDERS:
                    messages.append(f"providers.{provider_id} is not a known provider ({', '.join(KNOWN_PROVIDERS)})")
                if not isinstance(config, dict) or config.get("route") not in PROVIDER_ROUTES:
                    messages.append(f"providers.{provider_id}.route must be one of {PROVIDER_ROUTES}")
                elif config["route"] == "subscription" and provider_id not in SUBSCRIPTION_CAPABLE_PROVIDERS:
                    messages.append(f"providers.{provider_id} has no subscription-route adapter; route must be api-key")
            if active is not None and active not in providers:
                messages.append(f"providers.active references unconfigured provider: {active}")
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
        or "is not a known provider" in message
        or "references unconfigured provider" in message
        for message in messages
    ):
        return ValidationResult(ManifestState.INVALID, str(path), messages)
    if stale:
        return ValidationResult(ManifestState.STALE, str(path), messages)
    return ValidationResult(ManifestState.VALID, str(path), messages)
