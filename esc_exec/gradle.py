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
    return {
        key: str(path.relative_to(component))
        for key, path in candidates.items()
        if path.exists()
    }

