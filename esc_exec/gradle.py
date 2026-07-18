from __future__ import annotations

import re
from pathlib import Path


INCLUDE_RE = re.compile(r'''include\s*\(\s*["'](:[^"']+)["']\s*\)''')
ROOT_NAME_RE = re.compile(r'''rootProject\.name\s*=\s*["']([^"']+)["']''')


def detect_gradle_repository(root: Path) -> tuple[str, list[tuple[str, Path]]]:
    settings = next(
        (candidate for candidate in (root / "settings.gradle.kts", root / "settings.gradle") if candidate.exists()),
        None,
    )
    if settings is None:
        raise ValueError(f"No settings.gradle.kts or settings.gradle found under {root}")
    text = settings.read_text(encoding="utf-8")
    name_match = ROOT_NAME_RE.search(text)
    repository_id = name_match.group(1) if name_match else root.name
    components: list[tuple[str, Path]] = []
    for gradle_path in INCLUDE_RE.findall(text):
        relative = Path(*gradle_path.lstrip(":").split(":"))
        if (root / relative).is_dir():
            components.append((gradle_path.lstrip(":").replace(":", "-"), relative))
    if not components and (
        (root / "build.gradle.kts").exists() or (root / "build.gradle").exists()
    ):
        # A single-module Gradle repository (no include(...) subprojects, e.g. a
        # small published library) still has exactly one real, buildable component:
        # the root project itself. Without this, such a repository detects zero
        # components and can never be onboarded for dependency/impact tracking.
        components.append((repository_id, Path(".")))
    return repository_id, components


def component_structure(root: Path, relative: Path) -> dict[str, str]:
    component = root / relative
    candidates = {
        "source": component / "src/main/kotlin",
        "tests": component / "src/test/kotlin",
        "resources": component / "src/main/resources",
        "test_resources": component / "src/test/resources",
        "migrations": component / "src/main/resources/db/migration",
        "documentation": component / "README.md",
        "build": next(
            (path for path in (component / "build.gradle.kts", component / "build.gradle") if path.exists()),
            component / "build.gradle.kts",
        ),
    }
    paths = {
        key: str(path.relative_to(component))
        for key, path in candidates.items()
        if path.exists()
    }
    if "source" not in paths and "tests" not in paths:
        # Kotlin Multiplatform modules don't use the plain JVM src/main|test/kotlin
        # layout at all -- every source set (commonMain, androidMain, iosMain, ...)
        # lives directly under src/, conventionally suffixed Main/Test. Without this,
        # a KMP component's manifest ends up with no source/tests paths at all, and
        # task-context routing gives an AI agent no search_roots to look in for it --
        # found while onboarding two real KMP repositories end to end.
        source_root = component / "src"
        if source_root.is_dir():
            for entry in sorted(source_root.iterdir()):
                if not entry.is_dir() or not (entry / "kotlin").is_dir():
                    continue
                if entry.name.endswith("Test"):
                    paths[f"tests_{entry.name}"] = str((entry / "kotlin").relative_to(component))
                elif entry.name.endswith("Main"):
                    paths[f"source_{entry.name}"] = str((entry / "kotlin").relative_to(component))
    return paths

