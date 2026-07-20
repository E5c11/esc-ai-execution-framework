from __future__ import annotations

from pathlib import Path
from typing import Protocol

from esc_exec.gradle import detect_gradle_repository, unresolved_gradle_includes
from esc_exec.npm import detect_npm_repository


class BuildSystemAdapter(Protocol):
    name: str
    repository_type: str
    component_type: str

    def detects(self, root: Path) -> bool: ...
    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]: ...

    def unresolved(self, root: Path) -> list[str]:
        """
        Identifiers this adapter's own static detection found signal for but could
        not resolve to a real on-disk component, in this adapter's own ID shape
        (e.g. a Gradle project path like `:core:api`) -- the generic "Tier 1 is
        stuck here" signal a Tier 2 AI fallback keys off of, without the caller
        needing to know which adapter produced it. Required on every adapter
        (not optional/duck-typed) so a new adapter is always honest about the
        question, even when the honest answer is always `[]` (see NpmAdapter).
        """
        ...


class GradleAdapter:
    name = "gradle"
    repository_type = "gradle-multi-project"
    component_type = "gradle-module"

    def detects(self, root: Path) -> bool:
        return (root / "settings.gradle.kts").exists() or (root / "settings.gradle").exists()

    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]:
        return detect_gradle_repository(root)

    def unresolved(self, root: Path) -> list[str]:
        return unresolved_gradle_includes(root)


class NpmAdapter:
    name = "npm"
    repository_type = "npm-package"
    component_type = "npm-package"

    def detects(self, root: Path) -> bool:
        return (root / "package.json").exists()

    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]:
        return detect_npm_repository(root)

    def unresolved(self, root: Path) -> list[str]:
        # Workspace-member resolution (once built, see
        # plan/active/generic-multi-component-detection.md task 2) is glob
        # resolution against a real filesystem -- fully deterministic, nothing
        # left ambiguous by construction. This always returns [], not because
        # it's unimplemented, but because there's genuinely nothing to report.
        return []


ADAPTERS: list[BuildSystemAdapter] = [GradleAdapter(), NpmAdapter()]


def detect_build_system(root: Path) -> tuple[str, list[tuple[str, Path]], BuildSystemAdapter]:
    for adapter in ADAPTERS:
        if adapter.detects(root):
            repository_id, components = adapter.detect(root)
            return repository_id, components, adapter
    raise ValueError(f"No supported build-system adapter detected a build under {root}")


def component_id_from_identifier(identifier: str) -> str:
    """
    Normalizes an `unresolved()` identifier (whatever shape an adapter's own build
    configuration uses -- a Gradle project path like `:core:api`, an npm-workspace
    equivalent, ...) into a component ID, the same way every adapter's own `detect`
    already derives one deterministically (e.g. GradleAdapter's `gradle_path.lstrip(
    ":").replace(":", "-")`). Deliberately adapter-agnostic -- strips a leading `:`
    if present, then collapses `:`/`/` separators to `-` -- so a Tier 2 AI-resolved
    identifier gets a component ID consistent with what Tier 1 would have produced
    for the same identifier, regardless of which adapter it came from.
    """
    return identifier.lstrip(":").replace(":", "-").replace("/", "-")
