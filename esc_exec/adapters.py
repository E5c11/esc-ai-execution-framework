from __future__ import annotations

from pathlib import Path
from typing import Protocol

from esc_exec.gradle import detect_gradle_repository
from esc_exec.npm import detect_npm_repository


class BuildSystemAdapter(Protocol):
    name: str
    repository_type: str
    component_type: str

    def detects(self, root: Path) -> bool: ...
    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]: ...


class GradleAdapter:
    name = "gradle"
    repository_type = "gradle-multi-project"
    component_type = "gradle-module"

    def detects(self, root: Path) -> bool:
        return (root / "settings.gradle.kts").exists() or (root / "settings.gradle").exists()

    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]:
        return detect_gradle_repository(root)


class NpmAdapter:
    name = "npm"
    repository_type = "npm-package"
    component_type = "npm-package"

    def detects(self, root: Path) -> bool:
        return (root / "package.json").exists()

    def detect(self, root: Path) -> tuple[str, list[tuple[str, Path]]]:
        return detect_npm_repository(root)


ADAPTERS: list[BuildSystemAdapter] = [GradleAdapter(), NpmAdapter()]


def detect_build_system(root: Path) -> tuple[str, list[tuple[str, Path]], BuildSystemAdapter]:
    for adapter in ADAPTERS:
        if adapter.detects(root):
            repository_id, components = adapter.detect(root)
            return repository_id, components, adapter
    raise ValueError(f"No supported build-system adapter detected a build under {root}")
